"""M7 票 05：会话落盘与三层记忆组装（文件即数据库，沿 AgentStore 先例）。

一只会话＝一个 YAML：`data/agents/<uuid12>/sessions/<sid12>.yaml`——住智能体目录内，
删智能体连带清会话（rmtree 一把扫）；sid 与智能体 id 同款 hex12 焊死穿越面。
owner 裁决（2026-09-14）：落盘替代 spec 原「内存 dict/重启丢历史」——重启历史仍在。
坏文件如实报错不静默吞（`all()` 整列报错＝AgentStore 同款纪律）；懒建档：
未知合法 sid＝空会话（"＋ 新建会话"＝换 id、下一问开新档），首问落盘才建文件。

三层记忆（图侧载荷契约见 graph/state.py，本模块是唯一组装者）：
- L1 工作记忆＝最近一轮**成功**的完整 SQL＋结果头部摘要 → draft；failed 轮不给草稿
  （"错误草稿不传染"最保守读法）。话题连续性由模型隐式判（owner 裁 2026-09-15
  票 09：fresh_topic 人肉闸撤销——用户永远直接打字换题、业界无此闸先例；
  "无关则忽略"授权进 prompt 措辞，见 graph/prompts.py）。
- L2 情节记忆＝**预算驱动窗口**（M9 票 05，spec §二 Q3：K=5 废除为规则、降为默认
  换算结果）→ turns（failed 行只留问题、如实标失败）＋**滚存摘要链**（spec §二 Q7：
  滑出窗口的轮不消失——懒补 catch_up_digest 在下一问组装前顺手补齐：每滑出轮一行
  冻存摘要、每满一批折一条标轮次范围的段落行、段落行再溢出丢最老；落盘＝会话档
  可选尾键 digest_lines＋digest_upto 游标，无尾键旧档逐字节现状）。
- L3 归档＝turns 全史，**永不进 prompt**——切窗只发生在 build_session_context。
结果集只注摘要（result_head：标量→值；否则头部行）——全量展示行只活在轮次
answer 载荷里供回放，不进任何上下文。口径注入照旧走请求显式 evidence > 智能体
业务知识的票 02.5 语义（owner 裁 2026-09-15：撤销会话级口径叠加框）。
"""
import uuid
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from qadata.graph.gssc import count_tokens
from qadata.graph.prompts import (
    FAILED_TURN_SUFFIX,
    MEMORY_TURN_PREFIX,
    digest_fold,
    digest_line_is_para,
    digest_para_prefix,
    digest_prompt,
    digest_turn_prefix,
)
from qadata.llm.tracing import BEIJING, timed_invoke
from qadata.types import QueryResult
from qadata.web._fs import atomic_write
from qadata.web.agents import AgentNotFound, AgentStore, is_hex12

# L2 预算驱动窗口（M9 票 05／spec §二 Q3）：从最近往回收原文轮直至 token 预算
# （tiktoken cl100k 计量，词表同票 04 硬资产）。560＝票 04 现实最坏记忆行实测
# 104 tok×5＝520 容得下、×6 出——**K=5 是默认换算结果而非规则**（BIRD 评测形态
# 逐题单轮、短会话全收，两态均零触发；定阈判据落档 .scratch/qadata-m9/）。
# "预算内从近往远"的聚合闸＝窗口吃本预算、摘要面吃结构封顶（10 段＋<10 行 ≈700 tok）、
# 真超有票 04 保险丝按 Zone 硬砍兜底——三层各有界，不设第二本账（双轴评审 a2 采此读法）。
MEMORY_TOKEN_BUDGET = 560
# 滚存摘要链批参（spec §二 Q7 单层归并）：满一批＝折一条段落行，同时是单次懒补
# 的轮数上限（欠账无界防护——多次连问逐批推进）；段落行存量上限＝溢出丢最老
DIGEST_BATCH = 10
DIGEST_PARA_CAP = 10
# 结果头部摘要的行数上限（喂 L1/L2 的记忆行，远小于显示行数——全量行不进上下文）
HEAD_ROWS = 3

SESSIONS_SUBDIR = "sessions"  # 票 04 起对 feedback 公开（旁挂档同目录单源，防两处字面量漂移）


class SessionStoreError(ValueError):
    """会话存储面的诚实拒绝（坏文件/越界输入/非法 id，一律不带病运行）。"""


class SessionNotFound(SessionStoreError):
    """查无此会话（含非法 sid）——API 面映射 404。"""


@dataclass(frozen=True)
class Session:
    id: str
    turns: tuple[dict[str, Any], ...] = ()  # L3 全史；轮形见 _validated_turn
    # fresh_topic 闸已撤（owner 裁 2026-09-15 票 09）——旧档案残留键读取忽略（_read）
    # M9 票 05 滚存摘要链（spec §二 Q7）：entries 时间升序、统一形 {turn, ts, line}
    # （line＝系统严格拼装前缀「第N轮：」行链 或「第a-b轮：」段落行，判别 digest_line_is_para）
    digest_lines: tuple[dict[str, Any], ...] = ()
    digest_upto: int = 0  # 游标＝已冻存摘要覆盖 turns[:upto]（懒补只从此往后收欠账）


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


def _turn_cost(t: dict[str, Any]) -> int:
    """一条记忆行的近似 token 料（按渲染同形料计量——MEMORY_TURN_PREFIX/
    FAILED_TURN_SUFFIX 共读防错价：措辞漂移会静默改窗口，双轴评审追补）：
    成功行＝问＋SQL＋结果三段、失败行＝单行；近似只许保守方向，精确闸是票 04 保险丝。"""
    q = str(t["question"])
    if t["failed"]:
        return count_tokens(f"{MEMORY_TURN_PREFIX}{q}{FAILED_TURN_SUFFIX}")
    return count_tokens(f"{MEMORY_TURN_PREFIX}{q}\n  SQL：{(t.get('answer') or {}).get('sql') or ''}"
                        f"\n  结果：{t.get('row_count')} 行；{t.get('head') or ''}")


def _window_count(turns: tuple[dict[str, Any], ...]) -> int:
    """预算驱动窗口（M9 票 05）：从最近往回收原文轮，token 预算内全收、越界即止。
    懒补的欠账边界与此同源（滑出窗口＝待摘要），两处绝不允许各切一刀。"""
    spent, n = 0, 0
    for t in reversed(turns):
        cost = _turn_cost(t)
        if spent + cost > MEMORY_TOKEN_BUDGET:
            break
        spent += cost
        n += 1
    return n


def _window_slice(turns: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]:
    """窗口原文切片（唯一切法单源——build_session_context/测试共读，防各处各切一刀）。"""
    n = _window_count(turns)
    return turns[len(turns) - n:] if n else ()


def build_session_context(session: Session) -> dict | None:
    """会话 → `session_context` 图侧载荷（{"turns": 窗口原文切片, "draft": L1|None,
    摘要在场时加 "digest_lines"}）。

    None＝无史可注（新会话/清场后首问），等价关态、prompt 逐字节一致。
    L2 窗口＝预算驱动（_window_count，K=5 是其默认换算结果）：failed 轮剥净 SQL 与
    结果（只留问题供消解，如实标失败）；滚存摘要（行链＋段落行）只随存档携带，
    渲染归 prompts.format_session_history（无摘要＝逐字节现状，姊妹钉）。
    L1 草稿只认最近一轮成功（票 09 撤人肉闸后，"用不用"交 generate prompt 的
    显式授权措辞＋沙箱/verify 兜底——连续性模型隐式判，零新增调用）。"""
    if not session.turns:
        return None
    window = []
    for t in _window_slice(session.turns):
        if t["failed"]:
            window.append({"question": t["question"], "sql": None, "row_count": None,
                           "head": None, "failed": True})
        else:
            window.append({"question": t["question"], "sql": t["answer"].get("sql"),
                           "row_count": t["row_count"], "head": t["head"],
                           "failed": False})
    draft = None
    last = session.turns[-1]
    if not last["failed"]:
        sql = last["answer"].get("sql")
        if sql:
            draft = {"sql": sql, "head": last["head"] or ""}
    ctx: dict[str, Any] = {"turns": window, "draft": draft}
    if session.digest_lines:
        ctx["digest_lines"] = [dict(e) for e in session.digest_lines]
    return ctx


def _digest_batch_src(turns: tuple[dict[str, Any], ...], start: int, stop: int) -> str:
    """懒补 prompt 的轮次素材块（turn 编号 1-based，与会话档下标一致可回查）。"""
    parts = []
    for i in range(start, stop):
        t = turns[i]
        label = digest_turn_prefix(i + 1)
        if t["failed"]:
            parts.append(f"{label}问题「{t['question']}」（该轮查询失败，无可靠结果）")
        else:
            parts.append(f"{label}问题「{t['question']}」"
                         f"SQL「{(t.get('answer') or {}).get('sql') or '（未提取到合法 SQL）'}」"
                         f"结果「{t.get('row_count')} 行；{t.get('head') or '（无摘要）'}」")
    return "\n".join(parts)


def _parse_digest(text: str, batch: list[int], fold_pool: list[dict[str, Any]]):
    """严格回读：要求的每个前缀恰好命中一次、正文非空、无多余行——不合即 None
    （宁可不补不可补错；失败游标不动，下次再补）。前缀＝系统拼装的单轮/范围编号，
    模型只填「轮：」之后的正文。"""
    expected: dict[str, str | None] = {digest_turn_prefix(t + 1): None for t in batch}
    para_prefix = (digest_para_prefix(fold_pool[0]["turn"], fold_pool[-1]["turn"])
                   if fold_pool else None)
    if para_prefix:
        expected[para_prefix] = None
    for raw in text.splitlines():
        ln = raw.strip()
        if not ln:
            continue
        key = next((p for p, v in expected.items() if v is None and ln.startswith(p)), None)
        if key is None or not ln[len(key):].strip():  # 多余行/未命中前缀/空正文＝整次不收
            return None
        expected[key] = ln  # 存全行（系统前缀原样保留——渲染/折段/淘汰都直读此串）
    if any(v is None for v in expected.values()):
        return None
    lines = {t: str(expected[digest_turn_prefix(t + 1)]) for t in batch}
    return {"lines": lines, "para": expected[para_prefix] if para_prefix else None}


def catch_up_digest(session: Session, *, llm, tracer=None) -> Session:
    """滚存摘要链懒补（M9 票 05，spec §二 Q7）：组装下一问前**同步**把欠账补齐——
    不开后台任务、不逐轮咀嚼。每滑出轮一行冻存摘要（压手＝正文 LLM 本体、一次调用
    补多轮 ≤DIGEST_BATCH 轮）；开工时既有行链已满一批（≥DIGEST_BATCH 行）＝同一调用
    顺手折一条标轮次范围的段落行（冻存、永不复压——被否的滚动重压就此止步）；段落行
    再溢出＝丢最老（有界形态，注入侧最坏 ≈10 段＋<10 行）。游标 digest_upto 随补齐
    推进；滑出≠丢失（L3 全史在档）。无新滑出＝不空转开调用（满而未折的行链由批上限
    封顶 ≤19 行、无注入压力，折段顺延至下次带欠账的补齐——双轴评审 a1 采此读法在册）。

    失败纪律：调用挂/回读不合＝入账（digest outcome 行）不拦本轮答题（老链＋窗口＋
    票 04 硬砍保险丝兜底），游标不动、下次再补。调用本体经 timed_invoke 真名入账
    （有 token 标记＝如实计一次 LLM 调用；outcome 行无 token 标记＝不烧调用数，
    budget_fuse 先例）。短会话无滑出＝原对象返回、零调用零花费（默认形态零触发）。

    ponytail: 第二层归并不做（spec §五 被否在案）——触发判据＝会话奔 500 轮量级
    （段落行数持续逼近 DIGEST_PARA_CAP、"丢最老段"开始伤答案质量的可查证据出现时），
    做法＝段落行再折超段（冻存纪律同延、永不再压已冻段）。"""
    end = len(session.turns) - _window_count(session.turns)
    if end <= session.digest_upto:
        return session
    batch = list(range(session.digest_upto, min(end, session.digest_upto + DIGEST_BATCH)))
    paras = [e for e in session.digest_lines if digest_line_is_para(str(e.get("line") or ""))]
    plain = [e for e in session.digest_lines if not digest_line_is_para(str(e.get("line") or ""))]
    fold_pool = plain[:DIGEST_BATCH] if len(plain) >= DIGEST_BATCH else []
    prompt = digest_prompt(
        _digest_batch_src(session.turns, batch[0], batch[-1] + 1),
        digest_fold(fold_pool[0]["turn"], fold_pool[-1]["turn"],
                    [str(e["line"]) for e in fold_pool]) if fold_pool else "")
    reason = ""
    try:
        text: str | None = str(timed_invoke(llm, prompt, "digest", tracer))
    except Exception as e:  # noqa: BLE001 摘要挂＝不拦答题（票 05 失败纪律）：入账、照常、下次再补
        text, reason = None, f"digest 调用失败：{str(e)[:160]}"
    parsed = _parse_digest(text, batch, fold_pool) if text is not None else None
    if parsed is None:
        if tracer is not None:
            tracer.log("digest", outcome="failed", pending=end - session.digest_upto,
                       reason=reason or "digest 输出不合模板")
        return session
    if fold_pool:
        paras = (paras + [{"turn": fold_pool[0]["turn"], "ts": turn_ts(),
                           "line": parsed["para"]}])[-DIGEST_PARA_CAP:]
        plain = plain[DIGEST_BATCH:]
    lines = plain + [{"turn": t + 1, "ts": turn_ts(), "line": parsed["lines"][t]}
                     for t in batch]
    if tracer is not None:
        tracer.log("digest", outcome="ok", digested=len(batch), folded=1 if fold_pool else 0)
    return replace(session, digest_lines=tuple(paras + lines), digest_upto=batch[-1] + 1)


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
        return self._agents.agent_dir(agent_id) / SESSIONS_SUBDIR

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
        # 未知键一律忽略——已撤销的旧机制（overlay、fresh_topic）残留档案仍可正常读取
        return Session(
            id=f.stem,
            turns=tuple(self._validated_turn(t, f) for t in turns),
            **self._validated_digest(data, f, len(turns)),
        )

    @staticmethod
    def _validated_digest(data: dict, f: Path, n_turns: int) -> dict[str, Any]:
        """M9 票 05 尾键（chart/feedback 缺省先例）：旧档无键＝() /0，装载形状与入档前
        逐字节一致；有键则形状如实校验（坏摘要链不装没看见，同坏轮纪律）。"""
        raw = data.get("digest_lines")
        if raw is None:
            return {}
        if not isinstance(raw, list) or any(
                not isinstance(e, dict) or not isinstance(e.get("turn"), int)
                or not isinstance(e.get("ts"), str)
                or not isinstance(e.get("line"), str) or not e["line"].strip() for e in raw):
            raise SessionStoreError(f"摘要链形状不正：{f.name}")
        upto = data.get("digest_upto")
        if not isinstance(upto, int) or isinstance(upto, bool) or not 0 <= upto <= n_turns:
            raise SessionStoreError(f"摘要链游标不正：{f.name}")
        return {"digest_lines": tuple(raw), "digest_upto": upto}

    @staticmethod
    def _validated_turn(t: Any, f: Path) -> dict[str, Any]:
        if (not isinstance(t, dict) or not isinstance(t.get("question"), str)
                or not isinstance(t.get("failed"), bool)
                or not isinstance(t.get("answer"), dict)):
            raise SessionStoreError(f"会话轮次形状不正：{f.name}")
        return {**t, "row_count": t.get("row_count") if isinstance(t.get("row_count"), int) else None,
                "head": str(t["head"]) if t.get("head") is not None else ""}

    def _dump(self, f: Path, session: Session) -> None:
        body = {"id": session.id, "turns": [dict(t) for t in session.turns]}
        if session.digest_lines:  # 票 05 可选尾键（与游标同进同出）：无摘要＝文件形状与入档前逐字节一致
            body["digest_lines"] = [dict(e) for e in session.digest_lines]
            body["digest_upto"] = session.digest_upto
        # 票 01：写经唯一入口收口（tmp+fsync+os.replace），crash 不出半档
        atomic_write(f, yaml.safe_dump(body, allow_unicode=True, sort_keys=False))


def turn_ts() -> str:
    """轮档 ts 唯一式（M9 票 01：web trace 的 turn_ts 串联键共用——「与 feedback 锚点
    同源」钉到表达式级。归档在收口盖、trace 在起念盖，秒级差在册；session_id 是主过滤键）。"""
    return datetime.now(BEIJING).isoformat(timespec="seconds")


def trail_entry(frame: dict[str, Any]) -> dict[str, Any] | None:
    """帧 → trail 条目（M9 票 02，裁决链 spec §三 Q9）：步骤帧＋tool 帧**原样入档**、
    thinking 帧拒收（直播安慰剂，内容已凝结进答案；内部全量看 Langfuse）。

    原样＝不挑键不抄字面——trail 条目与直播帧同形同源（前端回放复用 Console
    同组件同形状），帧契约再演进也不会在这里漂出第二份形状。精简相对的是
    thinking 流（单流可数百帧 ≤2000 字），其余帧本就一行字段 ≈1KB/轮。

    ponytail: 返回同一 dict 引用（与直播帧别名）——帧 emit 即弃、无人回改，现实安全；
    若哪天有节点复用已发帧，这里换 dict(frame)。"""
    return None if frame.get("kind") == "thinking" else frame


def trail_sink(on_event, out: list[dict[str, Any]]):
    """包一层的 on_event：帧先精简入 trail 留痕表，再转原消费者（obs.mirror 同款姿势）。

    原消费者缺位（阻塞 /api/ask）＝打 qadata_no_stream 标——thinking 流式旁路只该被
    直播消费者开启，入档观测不得改变 LLM 调用形态（M9 票 01 姊妹纪律，_llm 闸认此标）。
    thinking 帧即便在场也进不了 trail（trail_entry 拒收），入档面与直播面各走各的。"""

    def cb(frame: dict[str, Any]) -> None:
        entry = trail_entry(frame)
        if entry is not None:
            out.append(entry)
        if on_event is not None:
            on_event(frame)

    if on_event is None:
        cb.qadata_no_stream = True  # type: ignore[attr-defined]
    return cb


def append_turn(session: Session, question: str, *, res: QueryResult | None,
                failed: bool, payload: dict[str, Any],
                trail: list[dict[str, Any]] | None = None) -> Session:
    """跑完一问 → 新会话对象（纯函数，落盘归 caller）。failed 轮如实标失败并剥净
    结果摘要（不给下游留草稿素材）。

    trail（M9 票 02 可选尾键，chart/feedback 先例）＝该轮步骤帧＋tool 帧精简留痕
    （会话档管「用户当时看见什么」，trace 管「机器内部怎么跑」——分工入册）；
    缺省/空＝不写键，轮条目与入档前逐字节一致（eval/CLI 与无会话通道零染指）。
    票 05 摘要链字段随 replace 原样携带（L3 永不截尾＝游标对 turns 下标恒有效）。"""
    turn = {"question": question,
            "ts": turn_ts(),
            "failed": failed,
            "row_count": res.row_count if (res and not failed) else None,
            "head": "" if failed else result_head(res),
            "answer": payload}
    if trail:
        turn["trail"] = list(trail)
    return replace(session, turns=session.turns + (turn,))
