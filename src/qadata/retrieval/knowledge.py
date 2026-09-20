"""M10 票 04 口径字典进料——智能体域档面（spec §二 Q8／ADR-0004/0006；查询路＝票 05）。

口径字典＝人进料的业务口径条目，住 `<智能体目录>/knowledge.yaml`（文件即数据库、
跟智能体走、删智能体连带清——examples.yaml 同域同纪律；ADR-0004 人进料域）。
**唯一进料口＝`qadata knowledge-feed` 显式管理动作**（examples-sign 同族）——
问数路径永不进料、永不写档（零触扫描钉守进料侧；消费面在票 05 开，本票
"建了没人读"是预期状态）。

进料格式＝**md/txt/csv**、**条目级切块**（一条口径一块＝空行分隔的块/一行 csv，
否字数滑窗；否 Word/PDF ETL——解析依赖膨胀在册被否，升级判据注释留位：真有人
喂 docx 且量大到手工转 md 不可忍，才议 ETL 依赖）。块序＝文件序、条文本原样
（保形＝装载后注入形态与进料人看到的一致，不加工不美化）。

进料即向量化＝**整档一批 embed**（票 06「一次向量化调用＝档面本体」记法先例；
人料池天然小——几千条口径仍是秒级批，增量复用那套是库派生物的花费形态，
不适用于管理动作）。同文去重＝跨feed/文件内一钉（重复条目＝重复召回料，
白占注入位）；重喂补齐＝进料只增不删（字典是累积资产，清库靠删智能体）。
缺 embedder 配置＝**可落盘但向量化挂账明示**——无向量档照写（内容不丢），
装载闸如实拒绝带病档（挂账文案给人修的指路：配 QADATA_EMBED_MODEL 重喂即补齐）。

manifest＝(model_id, built_at)（智能体域无库可对账——库域四件套的 db/content_hash
两笔在库文件身上，此档不适用）。换 embedding 模型＝旧向量作废（整档 embed 天然
全重刷＋model_id 更新——例题库「自动出局」语义的进料面对齐，读侧判定归票 05）。

坏档纪律（examples/load_cards 同门）：形状不正整档如实炸 KnowledgeError，
调用方负责降级入账不静默——缺档＝None＝默认关＝逐字节现状。
"""
import csv
import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NamedTuple

import yaml

from qadata.llm.tracing import now_beijing

# atomic_write 唯一原子写入口复用（store.py 同门：跨层 import 好过三份
# tmp+fsync+os.replace 字面漂移）
from qadata.web._fs import atomic_write

KNOWLEDGE_FILENAME = "knowledge.yaml"  # 智能体目录内口径字典唯一档名（写入口单源＝feed_knowledge）
INTAKE_SUFFIXES = frozenset({".md", ".txt", ".csv"})  # 进料白名单（Word/PDF 被否在案）
_BLOCK_SPLIT = re.compile(r"\n[ \t]*\n")  # 空行＝条目边界（块内换行原样保留＝保形）


class KnowledgeError(ValueError):
    """口径字典档面的诚实拒绝（坏档/坏形状/坏向量——不带病召回）。"""


@dataclass(frozen=True)
class KnowledgeEntry:
    """一条口径＝一块料（text 原样、vec 进料当场整档 embed 的真值）。"""

    text: str
    vec: tuple[float, ...]


@dataclass(frozen=True)
class StoredKnowledge:
    """字典档装载形状。entries 保进料序（跨 feed＝旧档序＋新块文件序追加）。"""

    model_id: str
    entries: tuple[KnowledgeEntry, ...]


class KnowledgeFeed(NamedTuple):
    """feed_knowledge 回执（CLI 如实打印的料——花费与挂账不静默）。"""

    total: int  # 整档条目数（写后）
    added: int  # 本次新增（同文去重后）
    embedded: int  # 本次向量化发数（挂账＝0）


def _knowledge_file(agent_dir: str | Path) -> Path:
    return Path(agent_dir) / KNOWLEDGE_FILENAME


def _split_entries(raw: str, suffix: str) -> list[str]:
    """条目级切块单源：csv＝一行一条（非空单元格全角分号承接，metric_evidence_text
    同法）；md/txt＝空行分块、块内原样。空块/空行如实丢弃（不是条目，不是拒绝）。"""
    text = raw.strip()
    if not text:
        return []
    if suffix == ".csv":
        out = []
        for rec in csv.reader(io.StringIO(text)):
            cells = [c for c in (x.strip() for x in rec) if c]
            if cells:
                out.append("；".join(cells))
        return out
    return [b.strip() for b in _BLOCK_SPLIT.split(text) if b.strip()]


def _read_body(agent_dir: str | Path) -> tuple[str, list[dict]] | None:
    """档体读取＋形状闸（text 必填非空；vec 不在此闸——挂账宽容归进料侧，
    向量完整性归 load_knowledge 的召回侧闸）。缺档＝None（与空文件的空池分家——
    load 面 None＝默认关现状、空池＝建过但没料）。"""
    f = _knowledge_file(agent_dir)
    if not f.is_file():
        return None
    try:
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as e:
        raise KnowledgeError(f"口径字典读不动：{f.name}（{e}）") from e
    if data is None:
        return "", []  # 空文件＝空池（examples 懒建档同款宽容）
    if (not isinstance(data, dict) or not isinstance(data.get("manifest"), dict)
            or not isinstance(data.get("entries"), list)):
        raise KnowledgeError(f"口径字典形状不正（须为 {{manifest, entries}} 字典）：{f.name}")
    entries = []
    for i, e in enumerate(data["entries"]):
        if not isinstance(e, dict) or not str(e.get("text") or "").strip():
            raise KnowledgeError(f"口径字典条目形状不正（text 须非空）：{f.name} 第 {i + 1} 条")
        entries.append(e)
    return str(data["manifest"].get("model_id") or ""), entries


def load_knowledge(agent_dir: str | Path) -> StoredKnowledge | None:
    """装载＋全闸校验（缺档＝None＝默认关；坏档/缺向量/坏向量整档如实炸——
    调用方降级入账，recall/load_examples 同纪律）。挂账档（无向量）在此显形：
    报错文案直书修法，不静默放行半残料进召回。"""
    body = _read_body(agent_dir)
    if body is None:
        return None
    model_id, raw = body
    entries: list[KnowledgeEntry] = []
    for i, e in enumerate(raw):
        vec = e.get("vec")
        if vec is None:
            raise KnowledgeError(
                f"口径字典向量化挂账：{KNOWLEDGE_FILENAME} 第 {i + 1} 条无向量"
                "——配 QADATA_EMBED_MODEL 后重喂进料即补齐（票 05 查询路对此降级入账）")
        if (not isinstance(vec, list) or not vec
                or any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in vec)):
            raise KnowledgeError(f"口径字典向量列不正：{KNOWLEDGE_FILENAME} 第 {i + 1} 条")
        entries.append(KnowledgeEntry(text=str(e["text"]).strip(),
                                      vec=tuple(float(x) for x in vec)))
    if entries and not model_id:  # 有向量却无 model_id＝手改坏的自相矛盾档，宁炸不带病
        raise KnowledgeError(f"口径字典 manifest 缺 model_id——不带病召回：{KNOWLEDGE_FILENAME}")
    return StoredKnowledge(model_id=model_id, entries=tuple(entries))


def feed_knowledge(agent_dir: str | Path, source: str | Path,
                   embedder: Any | None) -> KnowledgeFeed:
    """进料（唯一写入口，CLI 管理动作专供）：读 md/txt/csv→条目级切块→与旧档
    同文去重合并（旧序＋新块文件序追加）→**整档一批 embed**→原子落盘。
    切出 0 条＝如实拒收（不空写、不白刷旧档）。
    embedder＝None（未配置 QADATA_EMBED_MODEL）＝可落盘但无向量挂账（装载闸
    拒读、重喂补齐）。embedder 面＝.model＋.embed（EmbeddingsClient/FakeEmbedder
    同形，cards.build_index 同款鸭子）。端点炸＝如实上抛，旧档原样不动
    （embed 先于写——进料失败不连累已见账的料）。"""
    f = _knowledge_file(agent_dir)
    if not f.parent.is_dir():
        raise KnowledgeError(f"智能体目录不存在：{f.parent}（口径字典不建孤儿档）")
    suffix = Path(source).suffix.lower()
    if suffix not in INTAKE_SUFFIXES:
        raise KnowledgeError(
            f"进料只认 md/txt/csv：{suffix!r}（Word/PDF ETL 被否在册——spec §二 Q8，"
            "先手工转 md 再喂）")
    try:
        raw = Path(source).read_text(encoding="utf-8-sig")  # BOM 宽容；坏编码如实炸不 replace
    except UnicodeDecodeError as e:
        raise KnowledgeError(f"进料文件读不动（须 UTF-8）：{source}（{e}）") from e
    blocks = _split_entries(raw, suffix)
    if not blocks:
        raise KnowledgeError(f"进料文件切出 0 条目：{source}"
                             "（空档/全空行——不白刷整档，更不动已见账的档）")
    _, old = _read_body(agent_dir) or ("", [])  # 旧档坏＝整档炸，不带病覆盖人料；旧 model_id 不留（整档 embed 必刷新）
    bank: dict[str, None] = dict.fromkeys(str(e["text"]).strip() for e in old)
    added = 0
    for t in blocks:
        if t not in bank:
            bank[t] = None
            added += 1
    texts = list(bank)
    body_entries: list[dict] = [{"text": t} for t in texts]
    written_model = ""
    if embedder is not None:
        vectors = embedder.embed(texts)  # 整档一批＝「一次向量化调用＝档面本体」（票 06 记法）
        for e, v in zip(body_entries, vectors, strict=True):
            e["vec"] = list(v)
        written_model = str(embedder.model)
    atomic_write(f, yaml.safe_dump(
        {"manifest": {"model_id": written_model, "built_at": now_beijing()},
         "entries": body_entries}, allow_unicode=True, sort_keys=False))
    return KnowledgeFeed(total=len(texts), added=added,
                         embedded=len(texts) if embedder is not None else 0)
