"""M7-rev2 票 02.5：智能体存储——一只智能体＝一个目录（真空启动，零预置）。

data/agents/<uuid12>/{meta.yaml, source.sqlite}。名称是展示属性可重复、
uuid 才是键；删智能体＝删目录，生命周期零悬挂。业务知识双态互斥：
手动 evidence 文本，或 metrics_ref 引用指标注册表——**引用态读取期派生**
（口径随注册表动，比票 02 的加载期抄写更彻底：没有第二份文本存在）。
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

from qadata.graph.metrics import Metric, load_registry

# 智能体数据目录（仓库根 cwd 约定；data/ 已在 .gitignore，不入库）
DEFAULT_AGENTS_DIR = "data/agents"
# 单智能体预设问题上限（宁缺勿错，防无限列表吃空状态排版）
MAX_PRESET_QUESTIONS = 10
# sqlite 家族扩展名白名单——上传输入面只认本地 sqlite 文件形态
SQLITE_SUFFIXES = frozenset({".sqlite", ".sqlite3", ".db"})

_NAME_MAX = 50
_DESC_MAX = 500
_DATASOURCE_FILENAME = "source.sqlite"


class AgentStoreError(ValueError):
    """智能体存储面的诚实拒绝（带病配置与越界输入一律拒，不带病运行）。"""


class AgentNotFound(AgentStoreError):
    """查无此智能体（含非法 id）——API 面映射 404，与 400 的形态拒绝分家。"""


@dataclass(frozen=True)
class AgentMeta:
    id: str
    name: str
    description: str = ""
    evidence: str = ""  # 手动业务知识（后端字段名沿用 evidence＝BIRD/CLI 语境，UI 叫业务知识）
    metrics_ref: str = ""  # 指标注册表引用名；非空即引用态，evidence 必为空
    preset_questions: tuple[str, ...] = ()


_MISSING = object()


class AgentStore:
    """文件即数据库：meta.yaml 人可读可审（metrics/ 同款纪律），无索引大文件。"""

    def __init__(self, root: str | Path, *, metrics_dir: str | Path = "metrics"):
        self._root = Path(root)
        self._metrics_dir = Path(metrics_dir)

    # ── 查询 ────────────────────────────────────────────────────────

    def all(self) -> list[AgentMeta]:
        """全部智能体（id 字典序稳定）。任一目录读不动即整列报错——
        坏智能体不装没看见，静默消失＝对用户撒谎（永不编造的另一面）。"""
        if not self._root.is_dir():
            return []
        return [self._load(d) for d in sorted(self._root.iterdir()) if d.is_dir()]

    def get(self, agent_id: str) -> AgentMeta:
        return self._load(self._dir_of(agent_id))

    def registries(self) -> list[str]:
        """可引用的指标注册表名（metrics/*.yaml 文件主干）。"""
        if not self._metrics_dir.is_dir():
            return []
        return sorted(p.stem for p in self._metrics_dir.glob("*.yaml"))

    def datasource_path(self, meta: AgentMeta) -> Path | None:
        p = self._dir_of(meta.id) / _DATASOURCE_FILENAME
        return p if p.is_file() else None

    def effective_evidence(self, meta: AgentMeta) -> str:
        """问数实际注入的业务知识：引用态→读取期派生；手动态→原文。"""
        if meta.metrics_ref:
            f = self._metrics_dir_file(meta.metrics_ref)
            if not f.is_file():
                raise AgentStoreError(
                    f"引用的指标注册表不存在：metrics/{meta.metrics_ref}.yaml")
            return metric_evidence_text(meta.metrics_ref, load_registry(f))
        return meta.evidence

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
        """部分更新：只动传入字段。引用/手动双写在此互斥（护栏见 _validated）。"""
        meta = self.get(agent_id)
        merged = meta
        if (v := fields.get("name", _MISSING)) is not _MISSING:
            merged = replace(merged, name=self._checked_name(v))
        if (v := fields.get("description", _MISSING)) is not _MISSING:
            merged = replace(merged, description=self._checked_desc(v))
        if (v := fields.get("evidence", _MISSING)) is not _MISSING:
            merged = replace(merged, evidence=str(v or ""))
        if (v := fields.get("metrics_ref", _MISSING)) is not _MISSING:
            merged = replace(merged, metrics_ref=str(v or "").strip())
        if (v := fields.get("preset_questions", _MISSING)) is not _MISSING:
            merged = replace(merged, preset_questions=self._checked_questions(v))
        merged = self._validated(merged)
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
        dest.write_bytes(data)
        return dest

    # ── 内部 ────────────────────────────────────────────────────────

    def _metrics_dir_file(self, name: str) -> Path:
        return self._metrics_dir / f"{name}.yaml"

    def _validated(self, meta: AgentMeta) -> AgentMeta:
        if meta.metrics_ref:
            if not self._metrics_dir_file(meta.metrics_ref).is_file():
                raise AgentStoreError(
                    f"指标注册表不存在：metrics/{meta.metrics_ref}.yaml（可引用：{self.registries()}）")
            if meta.evidence:
                raise AgentStoreError(
                    "引用指标注册表时不得同时保存手动业务知识——双写＝第二真源；"
                    "先清空一头再换另一头")
        return meta

    def _dir_of(self, agent_id: str) -> Path:
        # id 只接受十六进制短串：路径穿越面从输入源头焊死
        if len(agent_id) != 12 or any(c not in "0123456789abcdef" for c in agent_id):
            raise AgentNotFound(f"非法智能体 id：{agent_id!r}")
        return self._root / agent_id

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
            evidence=str(data.get("evidence") or ""),
            metrics_ref=str(data.get("metrics_ref") or ""),
            preset_questions=tuple(str(q) for q in (data.get("preset_questions") or [])),
        )

    def _dump(self, d: Path, meta: AgentMeta) -> None:
        body = {"name": meta.name, "description": meta.description,
                "evidence": meta.evidence, "metrics_ref": meta.metrics_ref,
                "preset_questions": list(meta.preset_questions)}
        (d / "meta.yaml").write_text(
            yaml.safe_dump(body, allow_unicode=True, sort_keys=False), encoding="utf-8")

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


# ── 指标注册表→业务知识派生（迁移自票 02，读取期复用）─────────────


def metric_evidence_text(db_name: str, metrics: Sequence[Metric]) -> str:
    """从指标注册表确定性派生业务知识文本（演示摘录，零 LLM）。

    头行指回真源并标注「演示摘录，非第二真源」；每指标一行，多行 definition
    以全角分号承接（evidence 通道是喂 prompt 的自由文本，不炸排版）。
    """
    src = f"metrics/{db_name}.yaml"
    lines = [f"口径摘录（摘自 {src}，演示摘录，非第二真源，权威以指标注册表为准）："]
    for m in metrics:
        flat = "；".join(part.strip() for part in m.definition.splitlines() if part.strip())
        lines.append(f"- {m.display_name}（{m.name}）：{flat}")
    return "\n".join(lines)
