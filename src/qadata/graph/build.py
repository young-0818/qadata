"""图组装：M2 带自纠错反馈环（条件边①执行失败重试、②校验可疑重试）。"""
from langgraph.graph import END, START, StateGraph

from qadata.config import FALLBACK_SETTINGS, Settings, load_settings
from qadata.graph.nodes import make_nodes
from qadata.graph.state import AgentState
from qadata.llm.gateway import build_llm
from qadata.types import Answer


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


def build_graph(llm, tracer=None, settings: Settings | None = None, limiter=None):
    s = settings or FALLBACK_SETTINGS
    nodes = make_nodes(llm, tracer, settings=s, limiter=limiter)
    g = StateGraph(AgentState)
    for name in ("understand", "explore", "generate", "execute", "verify", "respond"):
        g.add_node(name, nodes[name])
    g.add_edge(START, "understand")
    g.add_edge("understand", "explore")
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
    return g.compile()


def run_question(db_path: str, question: str, evidence: str = "", llm=None,
                 tracer=None, settings: Settings | None = None, limiter=None) -> Answer:
    try:
        if llm is None:
            settings = settings or load_settings()
            llm = build_llm(settings)
        graph = build_graph(llm, tracer, settings=settings, limiter=limiter)
        final = graph.invoke({"db_path": db_path, "question": question, "evidence": evidence})
        return final["answer"]
    except Exception as e:  # noqa: BLE001 run_question 是最外层守护：有意收敛一切裸异常
        # 永不编造（面向 CLI 用户）：收敛图内未兜住的裸异常为诚实失败答案。
        return Answer(
            conclusion=f"未能完成查询：{e}",
            sql=None,
            result=None,
            failed=True,
            error_summary=str(e),
        )
