"""图组装：M2 带自纠错反馈环（条件边①执行失败重试、②校验可疑重试）。"""
from langgraph.graph import END, START, StateGraph

from qadata.config import FALLBACK_SETTINGS, Settings, load_settings
from qadata.graph.nodes import make_nodes
from qadata.graph.state import AgentState
from qadata.llm.gateway import build_llm
from qadata.types import Answer


def _route_after_understand(state: dict, metric_layer: bool) -> str:
    """指标层总开关（票 05）：关＝与纯 SQL 现状逐行为一致；开＝进 metric_match。

    注册表存在与否不在路由判（路由保持纯函数不摸文件系统）——节点无文件即零调用跳过。
    """
    return "metric_match" if metric_layer else "explore"


def _route_after_metric_match(state: dict) -> str:
    """命中（matched_metric 非空且已渲染）→ 直接执行；未命中/跳过 → 兜底路线。"""
    return "execute" if state.get("matched_metric") else "explore"


def _route_after_execute(state: dict, budget: int) -> str:
    """条件边①：失败且预算未尽 → 重试；失败且耗尽 → 兜底；成功 → 校验。

    指标路径优先判降级：模板执行失败 → 恰一次降回兜底（explore 补 schema 并清
    matched_metric，降级后回落既有环，不会二次降级）；降级同受预算闸，预算耗尽则诚实兜底。
    """
    if state.get("result") is None:
        if state.get("matched_metric"):
            # 模板失败降级：与常规环同样受预算闸（降级不额外赠预算，模板尝试已占一格）
            return "explore" if len(state.get("attempts", [])) < budget else "respond"
        return "generate" if len(state.get("attempts", [])) < budget else "respond"
    return "verify"


def _route_after_verify(state: dict, budget: int) -> str:
    """条件边②：通过 → 作答；可疑且预算未尽 → 换思路重试；可疑且耗尽 → 带标注作答。

    命中路径可疑不走裸 generate（explore 被跳过、db_schema 为空＝无米之炊）——
    经 explore 降回兜底进重试环；预算耗尽则维持带标注作答（数据真实）。
    """
    if state.get("verify_note") is None:
        return "respond"
    if state.get("matched_metric"):
        return "explore" if len(state.get("attempts", [])) < budget else "respond"
    return "generate" if len(state.get("attempts", [])) < budget else "respond"


def build_graph(llm, tracer=None, settings: Settings | None = None, limiter=None,
                skip_respond: bool = False, on_event=None):
    s = settings or FALLBACK_SETTINGS
    nodes = make_nodes(llm, tracer, settings=s, limiter=limiter, skip_respond=skip_respond,
                       on_event=on_event)
    g = StateGraph(AgentState)
    for name in ("understand", "metric_match", "explore", "generate", "execute",
                 "verify", "respond"):
        g.add_node(name, nodes[name])
    g.add_edge(START, "understand")
    g.add_conditional_edges(
        "understand",
        lambda st: _route_after_understand(st, s.metric_layer),
        {"metric_match": "metric_match", "explore": "explore"},
    )
    g.add_conditional_edges(
        "metric_match",
        _route_after_metric_match,
        {"execute": "execute", "explore": "explore"},
    )
    g.add_edge("explore", "generate")
    g.add_edge("generate", "execute")
    g.add_conditional_edges(
        "execute",
        lambda st: _route_after_execute(st, s.retry_budget),
        {"generate": "generate", "verify": "verify", "respond": "respond",
         "explore": "explore"},
    )
    g.add_conditional_edges(
        "verify",
        lambda st: _route_after_verify(st, s.retry_budget),
        {"generate": "generate", "respond": "respond", "explore": "explore"},
    )
    g.add_edge("respond", END)
    return g.compile()


def run_question(db_path: str, question: str, evidence: str = "", llm=None,
                 tracer=None, settings: Settings | None = None, limiter=None,
                 skip_respond: bool = False, on_event=None) -> Answer:
    """跑一题到底。on_event（票 03）＝节点级进度回调 `Callable[[dict], None]`，
    帧形如 {node, attempt, status}；缺省 None 时与现状逐行为一致（CLI/eval 调用面
    零改动，专测钉死于 tests/test_on_event.py）。"""
    try:
        if llm is None:
            settings = settings or load_settings()
            llm = build_llm(settings)
        graph = build_graph(llm, tracer, settings=settings, limiter=limiter,
                            skip_respond=skip_respond, on_event=on_event)
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
