"""M7 票 05：会话落盘与三层记忆组装（文件即数据库，沿 AgentStore 先例）。

一只会话＝一个 YAML：`data/agents/<uuid12>/sessions/<sid12>.yaml`——住智能体目录内，
删智能体连带清会话（rmtree 一把扫）；sid 与智能体 id 同款 hex12 焊死穿越面。
owner 裁决（2026-09-14）：落盘替代 spec 原「内存 dict/重启丢历史」——重启历史仍在。
坏文件如实报错不静默吞（`all()` 整列报错＝AgentStore 同款纪律）；懒建档：
未知合法 sid＝空会话（"＋ 新建会话"＝换 id、下一问开新档），首问落盘才建文件。

三层记忆（图侧载荷契约见 graph/state.py，本模块是唯一组装者，零 LLM 判定）：
- L1 工作记忆＝最近一轮**成功**的完整 SQL＋结果头部摘要 → draft；failed 轮不给草稿
  （"错误草稿不传染"最保守读法）；新话题清 L1 保 L2（fresh_topic 闸，落盘随会话）。
- L2 情节记忆＝最近 K=5 轮滑窗 → turns（failed 行只留问题、如实标失败）。
- L3 归档＝turns 全史，**永不进 prompt**——切窗只发生在 build_session_context。
结果集只注摘要（result_head：标量→值；否则头部行）——全量展示行只活在轮次
answer 载荷里供回放，不进任何上下文。优先级/合并（请求显式 > 会话叠加＋业务知识
拼接 > 空）在 web/app.py 收口，本模块只存文本、不掺判定。
"""
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from qadata.llm.tracing import BEIJING
from qadata.types import QueryResult
from qadata.web.agents import AgentNotFound, AgentStore, is_hex12

# L2 情节记忆窗口：最近 K 轮进 understand（spec 票 05；换窗＝记忆升级阶梯的触发器，勿随手调）
SESSION_MEMORY_K = 5
# 结果头部摘要的行数上限（喂 L1/L2 的记忆行，远小于显示行数——全量行不进上下文）
HEAD_ROWS = 3
# 会话级口径叠加文本上限（与智能体描述同量级，防无限长吃 prompt）
OVERLAY_MAX = 2000

_SESSIONS_SUBDIR = "sessions"


class SessionStoreError(ValueError):
    """会话存储面的诚实拒绝（坏文件/越界输入/非法 id，一律不带病运行）。"""


class SessionNotFound(SessionStoreError):
    """查无此会话（含非法 sid）——API 面映射 404。"""


@dataclass(frozen=True)
class Session:
    id: str
    overlay: str = ""  # 会话级口径叠加（常驻文本，每轮并进 evidence——拼接非覆盖）
    fresh_topic: bool = False  # 新话题闸：True＝下一问清 L1 保 L2（append 轮时复位）
    turns: tuple[dict[str, Any], ...] = ()  # L3 全史；轮形见 _validated_turn


def new_session_id() -> str:
    return uuid.uuid4().hex[:12]


def result_head(res: QueryResult | None, *, limit: int = HEAD_ROWS) -> str:
    """结果头部摘要（确定性纯函数，零 LLM）——三层记忆唯一认可的结果形态。

    标量（一行一列）→ 值本身；否则前 limit 行 `a | b` 串接。`QueryResult.rows`
    本就被显示上限截断，摘要再截是为 prompt 预算；row_count 真值另行在场。"""
    if res is None or not res.rows:
        return "0 行（未查询到数据）"
    if len(res.columns) == 1 and len(res.rows) == 1:
        return f"标量值 {res.rows[0][0]}"
    shown = res.rows[:limit]
    body = "；".join(" | ".join(str(v) for v in row) for row in shown)
    more = "…" if res.row_count > len(shown) else ""
    return f"头部 {len(shown)} 行：{body}{more}"


def build_session_context(session: Session) -> dict | None:
    """会话 → `session_context` 图侧载荷（{"turns": L2 切片, "draft": L1|None}）。

    None＝无史可注（新会话/清场后首问），等价关态、prompt 逐字节一致。
    L2 行取自最近 K 轮：failed 轮剥净 SQL 与结果（只留问题供消解，如实标失败）；
    L1 草稿仅认最近一轮成功且非新话题（fresh_topic 闸），随首问落盘复位。"""
    if not session.turns:
        return None
    window = []
    for t in session.turns[-SESSION_MEMORY_K:]:
        if t["failed"]:
            window.append({"question": t["question"], "sql": None, "row_count": None,
                           "head": None, "failed": True})
        else:
            window.append({"question": t["question"], "sql": t["answer"].get("sql"),
                           "row_count": t["row_count"], "head": t["head"],
                           "failed": False})
    draft = None
    last = session.turns[-1]
    if not last["failed"] and not session.fresh_topic:
        sql = last["answer"].get("sql")
        if sql:
            draft = {"sql": sql, "head": last["head"] or ""}
    return {"turns": window, "draft": draft}


class SessionStore:
    """文件即数据库（AgentStore 同款）：会话文件人可读可审，无索引无缓存。"""

    def __init__(self, agents: AgentStore):
        self._agents = agents

    # ── 查询 ────────────────────────────────────────────────────────

    def load(self, agent_id: str, sid: str) -> Session:
        """懒建档：合法新 sid 且无文件＝空会话（首问落盘才建）。坏文件如实炸。"""
        self._checked_sid(sid)
        self._agent_exists(agent_id)
        f = self._file_of(agent_id, sid)
        if not f.is_file():
            return Session(id=sid)
        return self._read(f)

    def list(self, agent_id: str) -> list[dict[str, Any]]:
        """侧栏摘要（updated 新→旧）：只认有轮次的会话（懒建档不占侧栏）。
        任一文件读不动即整列报错——坏会话不装没看见（AgentStore.all 同款纪律）。"""
        self._agent_exists(agent_id)
        root = self._sessions_root(agent_id)
        out = []
        for f in sorted(root.glob("*.yaml")):
            s = self._read(f)
            if not s.turns:
                continue
            out.append({"id": s.id, "title": str(s.turns[0]["question"]),
                        "updated": str(s.turns[-1].get("ts") or ""),
                        "turn_count": len(s.turns)})
        out.sort(key=lambda d: d["updated"], reverse=True)
        return out

    def replay(self, agent_id: str, sid: str) -> Session:
        """历史重开：必须真存在（没问过话的 sid 无档可放）。"""
        s = self.load(agent_id, sid)
        if not self._file_of(agent_id, sid).is_file():
            raise SessionNotFound(f"会话不存在：{sid}")
        return s

    # ── 变更 ────────────────────────────────────────────────────────

    def save(self, agent_id: str, session: Session) -> None:
        self._checked_sid(session.id)
        self._agent_exists(agent_id)
        if len(session.overlay) > OVERLAY_MAX:
            raise SessionStoreError(f"会话口径叠加超长（≤{OVERLAY_MAX} 字）")
        d = self._sessions_root(agent_id)
        d.mkdir(parents=True, exist_ok=True)
        self._dump(d / f"{session.id}.yaml", session)

    # ── 内部 ────────────────────────────────────────────────────────

    def _checked_sid(self, sid: str) -> None:
        if not is_hex12(sid):
            raise SessionNotFound(f"非法会话 id：{sid!r}")

    def _agent_exists(self, agent_id: str) -> None:
        # save 会 mkdir 父目录——智能体不存在时必须拒（删智能体后写会话＝目录僵尸，
        # 还会把 AgentStore.all 整列拖炸；hex 检查不含存在性，这里补上）
        if not (self._agents.agent_dir(agent_id) / "meta.yaml").is_file():
            raise AgentNotFound(f"智能体不存在：{agent_id}")

    def _sessions_root(self, agent_id: str) -> Path:
        return self._agents.agent_dir(agent_id) / _SESSIONS_SUBDIR

    def _file_of(self, agent_id: str, sid: str) -> Path:
        return self._sessions_root(agent_id) / f"{sid}.yaml"

    def _read(self, f: Path) -> Session:
        try:
            data = yaml.safe_load(f.read_text(encoding="utf-8"))
        except (yaml.YAMLError, UnicodeDecodeError) as e:
            raise SessionStoreError(f"会话文件读不动：{f.name}（{e}）") from e
        if not isinstance(data, dict) or str(data.get("id") or "") != f.stem:
            raise SessionStoreError(f"会话文件形状不正：{f.name}")
        turns = data.get("turns")
        if not isinstance(turns, list):
            raise SessionStoreError(f"会话文件缺 turns 列表：{f.name}")
        return Session(
            id=f.stem,
            overlay=str(data.get("overlay") or ""),
            fresh_topic=bool(data.get("fresh_topic") or False),
            turns=tuple(self._validated_turn(t, f) for t in turns),
        )

    @staticmethod
    def _validated_turn(t: Any, f: Path) -> dict[str, Any]:
        if (not isinstance(t, dict) or not isinstance(t.get("question"), str)
                or not isinstance(t.get("failed"), bool)
                or not isinstance(t.get("answer"), dict)):
            raise SessionStoreError(f"会话轮次形状不正：{f.name}")
        return {**t, "row_count": t.get("row_count") if isinstance(t.get("row_count"), int) else None,
                "head": str(t["head"]) if t.get("head") is not None else ""}

    def _dump(self, f: Path, session: Session) -> None:
        body = {"id": session.id, "overlay": session.overlay,
                "fresh_topic": session.fresh_topic,
                "turns": [dict(t) for t in session.turns]}
        f.write_text(yaml.safe_dump(body, allow_unicode=True, sort_keys=False),
                     encoding="utf-8")


def append_turn(session: Session, question: str, *, res: QueryResult | None,
                failed: bool, payload: dict[str, Any]) -> Session:
    """跑完一问 → 新会话对象（纯函数，落盘归 caller）。failed 轮如实标失败并剥净
    结果摘要（不给下游留草稿素材）；fresh_topic 在此复位（新话题只挡下一问一次）。"""
    turn = {"question": question,
            "ts": datetime.now(BEIJING).isoformat(timespec="seconds"),
            "failed": failed,
            "row_count": res.row_count if (res and not failed) else None,
            "head": "" if failed else result_head(res),
            "answer": payload}
    return replace(session, fresh_topic=False, turns=session.turns + (turn,))
