"""M7-rev2 票 02.5：智能体存储——一只智能体＝一个目录（真空启动，零预置）。

data/agents/<uuid12>/{meta.yaml, source.sqlite}。名称是展示属性可重复、
uuid 才是键；删智能体＝删目录，生命周期零悬挂。指标注册表引用态（metrics_ref）
已随 M5 退役（ADR-0007）；旧 meta.yaml 残留键读取忽略（M7 退役键先例，兼容测在册）。
数据源＝浏览器上传唯一路，固定落盘 source.sqlite（原始文件名不进文件系统，
恶意名/路径穿越问题整体消失）；换库＝覆盖。本模块只搬文件，从不建数据库
连接——三层唯一入口钉测（AST 禁 sqlite3／导入零建连／问答走 open_readonly）
由 tests/test_web_api.py 迁移钉死。
"""
import shutil
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

import yaml

from qadata.web._fs import atomic_write

# 智能体数据目录（仓库根 cwd 约定；data/ 已在 .gitignore，不入库）
DEFAULT_AGENTS_DIR = "data/agents"
# 单智能体预设问题上限（宁缺勿错，防无限列表吃空状态排版）
MAX_PRESET_QUESTIONS = 10
# sqlite 家族扩展名白名单——上传输入面只认本地 sqlite 文件形态
SQLITE_SUFFIXES = frozenset({".sqlite", ".sqlite3", ".db"})

_NAME_MAX = 50
_DESC_MAX = 500
_DATASOURCE_FILENAME = "source.sqlite"


def is_hex12(raw: str) -> bool:
    """uuid4().hex[:12] 短串判定——智能体/会话 id 的路径穿越面从输入源头焊死（票 05 共用）。"""
    return len(raw) == 12 and all(c in "0123456789abcdef" for c in raw)


class AgentStoreError(ValueError):
    """智能体存储面的诚实拒绝（带病配置与越界输入一律拒，不带病运行）。"""


class AgentNotFound(AgentStoreError):
    """查无此智能体（含非法 id）——API 面映射 404，与 400 的形态拒绝分家。"""


@dataclass(frozen=True)
class AgentMeta:
    id: str
    name: str
    description: str = ""
    # evidence（手动业务知识）字段已随 ADR-0008 退役——口径唯一通道＝智能体目录
    # knowledge.yaml（knowledge-feed 双门进料）；旧 meta.yaml 残留键读取忽略（M7 先例）。
    preset_questions: tuple[str, ...] = ()


_MISSING = object()


class AgentStore:
    """文件即数据库：meta.yaml 人可读可审，无索引大文件。"""

    def __init__(self, root: str | Path):
        self._root = Path(root)

    # ── 查询 ────────────────────────────────────────────────────────

    def all(self) -> list[AgentMeta]:
        """全部智能体（id 字典序稳定）。任一目录读不动即整列报错——
        坏智能体不装没看见，静默消失＝对用户撒谎（永不编造的另一面）。"""
        if not self._root.is_dir():
            return []
        return [self._load(d) for d in sorted(self._root.iterdir()) if d.is_dir()]

    def get(self, agent_id: str) -> AgentMeta:
        return self._load(self._dir_of(agent_id))

    def datasource_path(self, meta: AgentMeta) -> Path | None:
        p = self._dir_of(meta.id) / _DATASOURCE_FILENAME
        return p if p.is_file() else None

    # ── 变更 ────────────────────────────────────────────────────────

    def create(self, name: str, description: str = "") -> AgentMeta:
        meta = AgentMeta(id=uuid.uuid4().hex[:12],
                         name=self._checked_name(name),
                         description=self._checked_desc(description))
        d = self._dir_of(meta.id)
        d.mkdir(parents=True, exist_ok=True)
        self._dump(d, meta)
        return meta

    def patch(self, agent_id: str, **fields) -> AgentMeta:
        """部分更新：只动传入字段。"""
        meta = self.get(agent_id)
        merged = meta
        if (v := fields.get("name", _MISSING)) is not _MISSING:
            merged = replace(merged, name=self._checked_name(v))
        if (v := fields.get("description", _MISSING)) is not _MISSING:
            merged = replace(merged, description=self._checked_desc(v))
        # evidence 请求字段＝不消费（退役键，见 AgentMeta 注记）
        if (v := fields.get("preset_questions", _MISSING)) is not _MISSING:
            merged = replace(merged, preset_questions=self._checked_questions(v))
        self._dump(self._dir_of(agent_id), merged)
        return merged

    def delete(self, agent_id: str) -> None:
        self.get(agent_id)  # 不存在→AgentNotFound，不误删他物
        shutil.rmtree(self._dir_of(agent_id))

    def store_datasource(self, agent_id: str, data: bytes, filename: str) -> Path:
        """上传字节覆盖落盘 source.sqlite。原始文件名只用于扩展名白名单检查，
        不进文件系统——落盘名固定，路径穿越/恶意文件名整类问题结构性消失。"""
        self.get(agent_id)  # 存在性检查（不存在抛错）
        if not data:
            raise AgentStoreError("上传内容为空")
        suffix = Path(filename or "").suffix.lower()
        if suffix not in SQLITE_SUFFIXES:
            raise AgentStoreError(
                f"只接受 sqlite 文件（{sorted(SQLITE_SUFFIXES)}）：{filename!r}")
        dest = self._dir_of(agent_id) / _DATASOURCE_FILENAME
        # 票 01：上传覆盖走原子写——覆盖坏一次＝整库损坏且用户以为换库成功，
        # 是本函数唯一升级点（存在性/白名单检查原样在前）
        atomic_write(dest, data)
        return dest

    # ── 内部 ────────────────────────────────────────────────────────

    def agent_dir(self, agent_id: str) -> Path:
        """公开目录定位（票 05 会话存储层共用）：hex12 防穿越在此单一真源；
        目录存在与否不在此判——存在性语义归各调用方（get/save 各自如实报错）。"""
        if not is_hex12(agent_id):
            raise AgentNotFound(f"非法智能体 id：{agent_id!r}")
        return self._root / agent_id

    def _dir_of(self, agent_id: str) -> Path:
        return self.agent_dir(agent_id)

    def _load(self, d: Path) -> AgentMeta:
        if not d.is_dir():
            raise AgentNotFound(f"智能体不存在：{d.name}")
        f = d / "meta.yaml"
        if not f.is_file():
            raise AgentStoreError(f"智能体目录缺 meta.yaml：{d}")
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("name"), str):
            raise AgentStoreError(f"meta.yaml 形状不正：{f}")
        return AgentMeta(
            id=d.name,
            name=data["name"],
            description=str(data.get("description") or ""),
            preset_questions=tuple(str(q) for q in (data.get("preset_questions") or [])),
        )

    def _dump(self, d: Path, meta: AgentMeta) -> None:
        body = {"name": meta.name, "description": meta.description,
                "preset_questions": list(meta.preset_questions)}
        atomic_write(d / "meta.yaml",
                     yaml.safe_dump(body, allow_unicode=True, sort_keys=False))

    @staticmethod
    def _checked_name(raw: str) -> str:
        name = (raw or "").strip()
        if not name or len(name) > _NAME_MAX:
            raise AgentStoreError(f"名称需非空且 ≤{_NAME_MAX} 字：{raw!r}")
        return name

    @staticmethod
    def _checked_desc(raw: str) -> str:
        desc = (raw or "").strip()
        if len(desc) > _DESC_MAX:
            raise AgentStoreError(f"描述超长（≤{_DESC_MAX} 字）")
        return desc

    @staticmethod
    def _checked_questions(raw: Sequence[str]) -> tuple[str, ...]:
        qs = tuple(str(q).strip() for q in (raw or []))
        if len(qs) > MAX_PRESET_QUESTIONS:
            raise AgentStoreError(f"预设问题最多 {MAX_PRESET_QUESTIONS} 条")
        if any(not q for q in qs):
            raise AgentStoreError("预设问题不允许空条目")
        return qs
