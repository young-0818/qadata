"""图组装：M1 线性图；M2 在此加入 verify 节点与自纠错条件边。"""
from langgraph.graph import END, START, StateGraph

from qadata.graph.nodes import make_nodes
from qadata.graph.state import AgentState
from qadata.llm.gateway import build_llm
from qadata.types import Answer


def build_graph(llm, tracer=None):
    nodes = make_nodes(llm, tracer)
    g = StateGraph(AgentState)
    g.add_node("understand", nodes["understand"])
    g.add_node("explore", nodes["explore"])
    g.add_node("generate", nodes["generate"])
    g.add_node("execute", nodes["execute"])
    g.add_node("respond", nodes["respond"])
    g.add_edge(START, "understand")
    g.add_edge("understand", "explore")
    g.add_edge("explore", "generate")
    g.add_edge("generate", "execute")
    g.add_edge("execute", "respond")
    g.add_edge("respond", END)
    return g.compile()


def run_question(db_path: str, question: str, evidence: str = "", llm=None, tracer=None) -> Answer:
    try:
        llm = llm or build_llm()
        graph = build_graph(llm, tracer)
        final = graph.invoke({"db_path": db_path, "question": question, "evidence": evidence})
        return final["answer"]
    except Exception as e:
        # 永不编造（面向 CLI 用户）：收敛图内未兜住的裸异常——坏库路径的 OperationalError、
        # 缺密钥的 RuntimeError、供应商 4xx 等，一律转为诚实失败答案（重试/退避留待 M2）。
        return Answer(
            conclusion=f"未能完成查询：{e}",
            sql=None,
            result=None,
            failed=True,
            error_summary=str(e),
        )
