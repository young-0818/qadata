"""库域档面（ADR-0004/0005）：库指纹目录＋manifest＋表卡档读写——纯文件，不碰 LLM。

归属裁决：表卡/值索引是**数据库文件的函数**，住 `data/indexes/<库指纹>/`，
serve 与 eval 共用一份；库指纹＝规范化绝对路径的 sha1 短前缀（换库＝换目录＝
天然作废，比 content_hash 更简单的失效判定，ADR-0005）。原地改文件而路径不变
＝已知在册盲区，重扫靠重新敲 index-build（生产升级路径＝mtime/size 入指纹，注释在册）。
manifest＝(content_hash, model_id, built_at)：content_hash 是构建期审计量与增量
复用参照；**换 embedding 模型＝model_id 不合＝整档作废**（读侧拒用降级、建侧全重
embed），不灰度（票 06"旧向量自动出局"语义扩展到表卡）。

坏档纪律沿 examples/load_examples 先例：**形状不正整档如实炸**（RetrievalError），
调用方负责降级入账不静默——缺档＝None＝默认关＝逐字节现状。
"""
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from qadata.llm.tracing import now_beijing
from qadata.web._fs import atomic_write  # 唯一原子写入口复用（web 层函数被库层引用＝

# 有意为之：tmp+fsync+os.replace 三份字面漂移比跨层 import 更坏；examples 先例同门）

DEFAULT_INDEX_DIR = "data/indexes"  # 仓库根 cwd 约定（TRACE_PATH/DEFAULT_AGENTS_DIR 同款）
CARDS_FILENAME = "table_cards.yaml"  # 库指纹目录内表卡档唯一档名（值索引票 02 另立档名）


class RetrievalError(ValueError):
    """检索档面的诚实拒绝（坏档/坏形状——不带病召回）。"""


def index_dir_for(db_path: str | os.PathLike, root: str | os.PathLike = DEFAULT_INDEX_DIR) -> Path:
    """库指纹＝规范化绝对路径（normcase：Windows 不分大小写、POSIX 照旧）的 sha1 前 12 位。"""
    fp = hashlib.sha1(os.path.normcase(str(Path(db_path).resolve())).encode("utf-8"))
    return Path(root) / fp.hexdigest()[:12]


def content_hash(db_path: str | os.PathLike) -> str:
    """库文件字节 sha256（分块读，百 MB 级也不整档进内存）。构建期记入 manifest。"""
    h = hashlib.sha256()
    with open(db_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


@dataclass(frozen=True)
class TableCard:
    """一张表卡＝表名＋卡片文本哈希＋向量（列永不单独进召回，spec §二 Q6）。"""

    table: str
    hash: str
    vec: tuple[float, ...]


@dataclass(frozen=True)
class StoredCards:
    """表卡档装载形状（manifest 头＋逐卡）。cards 保构建序＝同分决定性的库序。"""

    model_id: str
    content_hash: str
    cards: tuple[TableCard, ...]


def _validated_card(c: Any, i: int) -> TableCard:
    if (not isinstance(c, dict) or not str(c.get("table") or "").strip()
            or not str(c.get("hash") or "").strip()):
        raise RetrievalError(f"表卡条目形状不正（table/hash 须非空）：第 {i + 1} 条")
    vec = c.get("vec")
    if (not isinstance(vec, list) or not vec
            or any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in vec)):
        raise RetrievalError(f"表卡向量列不正：{c['table']}")
    return TableCard(table=str(c["table"]).strip(), hash=str(c["hash"]).strip(),
                     vec=tuple(float(x) for x in vec))


def load_cards(db_path: str | os.PathLike, *,
               root: str | os.PathLike = DEFAULT_INDEX_DIR) -> StoredCards | None:
    """装载＋形状校验（缺档＝None＝默认关；坏档如实炸，调用方降级入账——examples 同纪律）。"""
    f = index_dir_for(db_path, root) / CARDS_FILENAME
    if not f.is_file():
        return None
    try:
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as e:
        raise RetrievalError(f"表卡档读不动：{e}") from e
    if (not isinstance(data, dict) or not isinstance(data.get("manifest"), dict)
            or not isinstance(data.get("cards"), list)):
        raise RetrievalError("表卡档形状不正（须为 {manifest, cards} 字典）")
    m = data["manifest"]
    if not str(m.get("model_id") or "").strip() or not str(m.get("content_hash") or "").strip():
        raise RetrievalError("表卡档 manifest 缺 model_id/content_hash——不带病召回")
    return StoredCards(model_id=str(m["model_id"]), content_hash=str(m["content_hash"]),
                       cards=tuple(_validated_card(c, i) for i, c in enumerate(data["cards"])))


def write_cards(db_path: str | os.PathLike, model_id: str,
                cards: list[tuple[str, str, list[float]]], *,
                root: str | os.PathLike = DEFAULT_INDEX_DIR) -> Path:
    """原子落表卡档（构建唯一写入口）；返回索引目录。父目录自建（index-build 是管理动作）。"""
    body = {"manifest": {"model_id": model_id, "content_hash": content_hash(db_path),
                         "built_at": now_beijing(),
                         "db": os.path.normcase(str(Path(db_path).resolve()))},
            "cards": [{"table": t, "hash": h, "vec": list(v)} for t, h, v in cards]}
    d = index_dir_for(db_path, root)
    d.mkdir(parents=True, exist_ok=True)
    atomic_write(d / CARDS_FILENAME, yaml.safe_dump(body, allow_unicode=True, sort_keys=False))
    return d


def index_status(db_path: str | os.PathLike, embed_model: str, *,
                 root: str | os.PathLike = DEFAULT_INDEX_DIR) -> str:
    """serve 启动播报/验收用的一眼态：missing（缺档/空池）｜broken（坏档）｜
    stale（model_id 与当前向量化模型不合）｜ok。永不抛（播报面不连累起服）。"""
    try:
        stored = load_cards(db_path, root=root)
    except RetrievalError:
        return "broken"
    if stored is None or not stored.cards:
        return "missing"
    return "ok" if stored.model_id == embed_model else "stale"
