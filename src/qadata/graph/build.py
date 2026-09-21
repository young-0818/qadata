"""图组装：M2 带自纠错反馈环（条件边①执行失败重试、②校验可疑重试）。"""
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from qadata.config import FALLBACK_SETTINGS, Settings, load_settings
from qadata.graph.nodes import make_nodes
from qadata.graph.state import AgentState
from qadata.llm.gateway import build_llm
from qadata.obs import obs_for
from qadata.types import Answer


def _route_after_understand(state: dict, clarification: bool = False) -> str:
    """M8 票 03 首判（默认关）：understand 出口写了 answer（澄清轮，图侧唯一提前落
    answer 的节点）→ 直达 END，不进 explore/generate。关态 answer 键不可能被写入，
    判据随开关才启用（双保险）。

    M5 指标层已于 2026-09-21 退役（ADR-0007）——本路由不再有 metric_match 分支。"""
    if clarification and state.get("answer") is not None:
        return "END"
    return "explore"


def _route_after_execute(state: dict, budget: int) -> str:
    """条件边①：失败且预算未尽 → 重试；失败且耗尽 → 兜底；成功 → 校验。"""
    if state.get("result") is None:
        return "generate" if len(state.get("attempts", [])) < budget else "respond"
    return "verify"


def _route_after_verify(state: dict, budget: int) -> str:
    """条件边②：通过 → 作答；可疑且预算未尽 → 换思路重试；可疑且耗尽 → 带标注作答。"""
    if state.get("verify_note") is None:
        return "respond"
    return "generate" if len(state.get("attempts", [])) < budget else "respond"


def build_graph(llm, tracer=None, settings: Settings | None = None, limiter=None,
                skip_respond: bool = False, on_event=None, checkpointer=None,
                hitl: bool = False, recall=None, table_recall=None, value_link=None,
                knowledge_recall=None):
    s = settings or FALLBACK_SETTINGS
    nodes = make_nodes(llm, tracer, settings=s, limiter=limiter, skip_respond=skip_respond,
                       on_event=on_event, hitl=hitl, recall=recall,
                       table_recall=table_recall, value_link=value_link,
                       knowledge_recall=knowledge_recall)
    g = StateGraph(AgentState)
    for name in ("understand", "explore", "generate", "execute", "verify", "respond"):
        g.add_node(name, nodes[name])
    g.add_edge(START, "understand")
    # M8 票 03：澄清保险丝——关态路由 map 与今日逐分支一致（END 分支随开关才加，
    # 不靠运行时不可达兜形状）；开态仅多一条直达 END 的出口。
    understand_routes = {"explore": "explore"}
    if s.clarification:
        understand_routes = {"END": END, **understand_routes}
    g.add_conditional_edges(
        "understand",
        lambda st: _route_after_understand(st, s.clarification),
        understand_routes,
    )
    g.add_edge("explore", "generate")
    g.add_edge("generate", "execute")
    g.add_conditional_edges(
        "execute",
        lambda st: _route_after_execute(st, s.retry_budget),
        {"generate": "generate", "verify": "verify", "respond": "respond"},
    )
    g.add_conditional_edges(
        "verify",
        lambda st: _route_after_verify(st, s.retry_budget),
        {"generate": "generate", "respond": "respond"},
    )
    g.add_edge("respond", END)
    # M8 票 03 改判（经典 HITL）：checkpointer 缺省 None＝compile() 原形（关态/CLI/评测
    # 零染指）；web 开态传入共享 MemorySaver，澄清节点 interrupt() 的暂停态按 thread 存它。
    return g.compile(checkpointer=checkpointer)


def _final_answer(final: dict) -> Answer:
    """图终态 → Answer：__interrupt__ 面＝暂停等人（conclusion=澄清问、clarification=同文，
    failed=False——不是失败）；否则取 final 的 answer（路由保证必有）。"""
    intr = final.get("__interrupt__")
    if intr:
        ask = str((getattr(intr[0], "value", None) or {}).get("clarification") or "").strip()
        if ask:
            return Answer(conclusion=ask, clarification=ask)
        # 暂停面却拿不出问句＝半坏载荷，宁诚实失败不编造（永不编造纪律）
        return _honest_failure(RuntimeError("澄清暂停载荷缺 clarification 文本"))
    return final["answer"]


def _honest_failure(e: Exception) -> Answer:
    """最外层守护的失败形态（永不编造：面向 CLI/评测用户给规则化诚实说明）。"""
    return Answer(
        conclusion=f"未能完成查询：{e}",
        sql=None,
        result=None,
        failed=True,
        error_summary=str(e),
    )


def run_question(db_path: str, question: str, evidence: str = "", llm=None,
                 tracer=None, settings: Settings | None = None, limiter=None,
                 skip_respond: bool = False, on_event=None, session_context=None,
                 thread_id: str | None = None, checkpointer=None, obs=None,
                 recall=None, table_recall=None, value_link=None,
                 knowledge_recall=None) -> Answer:
    """跑一题到底。on_event（票 03）＝节点级进度回调 `Callable[[dict], None]`，
    帧形如 {node, attempt, status}；缺省 None 时与现状逐行为一致（CLI/eval 调用面
    零改动，专测钉死于 tests/test_on_event.py）。
    session_context（票 05）＝三层记忆载荷 {"turns": [...], "draft": ...|None}，
    仅此透传进初始状态供 understand/generate 消费；缺省 None 时初始状态与本参数
    存在前逐字节一致（单轮关态钉死于 tests/test_session_context.py）。
    thread_id＋checkpointer（M8 票 03 经典 HITL，owner 改判 2026-09-16）：成对给出时
    澄清走节点内 interrupt() 暂停（invoke 正常返回 __interrupt__ 面→_final_answer 收成
    澄清 Answer），人类补充经 resume_question 同 thread 续跑；缺省 None＝澄清落 answer
    直达 END（CLI/eval/无会话单轮形态，与本参数存在前逐行为一致）。
    obs（M9 票 01，qadata.obs.Obs）：帧流→OTel span 镜像器，缺省 None＝调用面零挂接
    （默认关逐字节照旧）；调用方不带＝出口开着时按问自造一条（CLI ask 形态，串联键 run_id）。
    recall（M9 票 06）：例题库召回回调（retrieval/examples.build_recall 装配，question→注入块），
    经 build_graph 闭包进 generate 的 Select 格——**不进状态键**（可调用对象×checkpointer
    序列化不相宜；session_context 显式载荷纪律在此不适用）；缺省 None＝逐字节现状
    （CLI/eval 零挂接）。
    table_recall（M10 票 01）：表卡粗召回调（retrieval/cards.build_table_recall 装配，
    question→候选表名 list|None），沿参进 explore 的大库分支（宽进窄出，spec §二 Q6）
    ——同 recall 的末位参与不进状态键纪律；serve 与 eval 共用库域档（ADR-0004），
    缺省 None＝大库现状一把梭逐字节一致。
    value_link（M10 票 03）：值链查询调（retrieval/values.build_value_link 装配，
    (question, intent, 入选表)→纸条块），沿参进 explore——值纸条贴 schema 上下文
    （小库大库都触发，spec §一）；同族末位参与不进状态键纪律，缺省 None＝逐字节现状。
    knowledge_recall（M10 票 05）：口径字典召回回调（retrieval/knowledge.
    build_knowledge_recall 装配，question→字典块），沿参进 generate 的 Select 格
    第四槽（消解后题面消费；M5 指标层已退役 ADR-0007）；同族末位参与不进状态键
    纪律，缺省 None＝逐字节现状。"""
    try:
        if llm is None:
            settings = settings or load_settings()
            llm = build_llm(settings)
        if obs is None:
            obs = obs_for(settings, question, {"run_id": getattr(tracer, "run_id", "")})
        if obs is not None:
            on_event = obs.mirror(on_event)
        # 成对纪律在唯一闸口守死（langgraph 会拒收"带 checkpointer 无 thread"的裸跑——
        # 单轮/CLI/评测自然双双缺省，直 END 形态与本参数存在前逐行为一致）
        hitl = thread_id is not None and checkpointer is not None
        graph = build_graph(llm, tracer, settings=settings, limiter=limiter,
                            skip_respond=skip_respond, on_event=on_event,
                            checkpointer=checkpointer if hitl else None, hitl=hitl,
                            recall=recall, table_recall=table_recall, value_link=value_link,
                            knowledge_recall=knowledge_recall)
        initial = {"db_path": db_path, "question": question, "evidence": evidence}
        if session_context is not None:
            initial["session_context"] = session_context
        final = (graph.invoke(initial, {"configurable": {"thread_id": thread_id}})
                 if hitl else graph.invoke(initial))
        return _final_answer(final)
    except Exception as e:  # noqa: BLE001 run_question 是最外层守护：有意收敛一切裸异常
        # 永不编造（面向 CLI 用户）：收敛图内未兜住的裸异常为诚实失败答案。
        return _honest_failure(e)
    finally:
        if obs is not None:  # 观测收口挂守护出口：暂停/失败/成功三路 span 都不悬空
            obs.close()


def resume_question(thread_id: str, supplement: str, llm=None,
                    tracer=None, settings: Settings | None = None, limiter=None,
                    skip_respond: bool = False, on_event=None, checkpointer=None,
                    obs=None, recall=None, table_recall=None, value_link=None,
                    knowledge_recall=None) -> Answer:
    """经典 HITL 续跑（M8 票 03 改判）：人类补充经 Command(resume) 送回暂停 thread，
    understand 节点重放（＝understand 共 2 次调用的既定代价）后走常规路线。
    session_context 不用重传——暂停态连记忆一起在 checkpoint 里。守护同 run_question：
    任何裸异常（含 thread 不存在/无 checkpointer）收敛为诚实失败。
    recall（M9 票 06）＝同 run_question 的召回回调（续跑轮的 generate 重新装配时消费），
    缺省 None＝逐字节现状。table_recall（M10 票 01）＝同 run_question（续跑轮 explore
    重新进图时消费），缺省 None＝现状逐字节一致。value_link（M10 票 03）＝同
    run_question（续跑轮 explore 消费合成全句为新题面重新抽词——memo 按题面天然分开，
    每问仍 ≤1 次向量调用），缺省 None＝逐字节现状。knowledge_recall（M10 票 05）＝同
    run_question（续跑轮 generate 以合成全句重召回——memo 同上按题面分开），缺省
    None＝逐字节现状。
    obs（M9 票 01）＝续跑问自己的 trace（暂停与续答是两条 web 请求、各一条，靠
    session_id 串联；缺省 None＝不镜像——无内部自造分支，续跑唯一入口是 web，串联键
    只有那里有，CLI/eval 不触此门）。"""
    try:
        if llm is None:
            settings = settings or load_settings()
            llm = build_llm(settings)
        if obs is not None:
            on_event = obs.mirror(on_event)
        graph = build_graph(llm, tracer, settings=settings, limiter=limiter,
                            skip_respond=skip_respond, on_event=on_event,
                            checkpointer=checkpointer, hitl=True, recall=recall,
                            table_recall=table_recall, value_link=value_link,
                            knowledge_recall=knowledge_recall)
        final = graph.invoke(Command(resume=supplement),
                             {"configurable": {"thread_id": thread_id}})
        return _final_answer(final)
    except Exception as e:  # noqa: BLE001 同 run_question：最外层守护收敛一切裸异常
        return _honest_failure(e)
    finally:
        if obs is not None:
            obs.close()
