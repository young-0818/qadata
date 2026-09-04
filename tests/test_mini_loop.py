import pytest

from qadata.lab.mini_loop import END, LastValue, MiniGraph, Topic


def test_lastvalue_overwrites_and_topic_appends():
    g = MiniGraph()
    g.set_channel("last", LastValue())
    g.set_channel("log", Topic())
    g.add_node("a", lambda s: {"last": 1, "log": ["x"]})
    g.add_node("b", lambda s: {"last": 2, "log": ["y"]})
    g.add_edge("a", "b")
    state = g.invoke({}, "a")
    assert state["last"] == 2      # 整值覆盖
    assert state["log"] == ["x", "y"]  # 追加合并


def test_default_channel_is_lastvalue():
    g = MiniGraph()
    g.add_node("a", lambda s: {"k": 1})
    g.add_node("b", lambda s: {"k": 2})
    g.add_edge("a", "b")
    assert g.invoke({}, "a")["k"] == 2


def test_conditional_edge_retry_shape():
    """复刻 qadata 自纠错形态：坏 SQL → 条件边回 generate，直至 END。"""
    g = MiniGraph()
    g.set_channel("attempts", Topic())

    def generate(s):
        return {"sql": "bad" if not s.get("attempts") else "good"}

    def execute(s):
        if s["sql"] == "bad":
            return {"attempts": [s["sql"]]}
        return {"result": 42}

    def router(s):
        if s.get("result") == 42:
            return END
        return "generate"

    g.add_node("generate", generate)
    g.add_node("execute", execute)
    g.add_edge("generate", "execute")
    g.add_conditional_edges("execute", router, {"generate": "generate", END: END})
    state = g.invoke({}, "generate")
    assert state["result"] == 42
    assert state["attempts"] == ["bad"]


def test_max_steps_guards_infinite_loop():
    g = MiniGraph()
    g.add_node("a", lambda s: {})
    g.add_edge("a", "a")
    with pytest.raises(RuntimeError, match="max_steps"):
        g.invoke({}, "a", max_steps=5)
