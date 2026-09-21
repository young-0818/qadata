from qadata.graph.build import _route_after_execute, _route_after_verify
from qadata.types import Answer, QueryResult, SqlAttempt


def _ok_result():
    return QueryResult(columns=["a"], rows=[(1,)], row_count=1, truncated=False, elapsed_ms=1)


def test_execute_error_with_budget_goes_generate():
    s = {"result": None, "attempts": [SqlAttempt(sql="x", error="e")]}
    assert _route_after_execute(s, budget=3) == "generate"


def test_execute_error_budget_exhausted_goes_respond():
    s = {"result": None, "attempts": [SqlAttempt(sql="x", error="e")] * 3}
    assert _route_after_execute(s, budget=3) == "respond"


def test_execute_success_goes_verify():
    s = {"result": _ok_result(), "attempts": [SqlAttempt(sql="x", row_count=1)]}
    assert _route_after_execute(s, budget=3) == "verify"


def test_verify_passed_goes_respond():
    s = {"verify_note": None, "attempts": [SqlAttempt(sql="x", row_count=1)]}
    assert _route_after_verify(s, budget=3) == "respond"


def test_verify_suspicious_with_budget_goes_generate():
    s = {"verify_note": "结果为空", "attempts": [SqlAttempt(sql="x", row_count=0)]}
    assert _route_after_verify(s, budget=3) == "generate"


def test_verify_suspicious_exhausted_goes_respond():
    s = {"verify_note": "结果为空", "attempts": [SqlAttempt(sql="x", row_count=0)] * 3}
    assert _route_after_verify(s, budget=3) == "respond"


from qadata.graph.build import _route_after_understand

# （M5 指标层路由族测已随 ADR-0007 退役删除）


# ── M8 票 03：澄清出口首判（纯函数直测；END 分支随开关才进 map 见 build_graph）──


def test_clarification_flag_on_answer_present_goes_end():
    """开态且 understand 出口已落 answer（澄清轮）→ 首判直达 END（answer 优先）。"""
    a = Answer(conclusion="澄清？", clarification="澄清？")
    assert _route_after_understand({"answer": a}, clarification=True) == "END"


def test_clarification_flag_off_never_ends():
    """关态逐行为一致：answer 键即便在（不可能，双保险）也不判 END；
    开态但无 answer（常规轮）→ 与关态同路由 explore。"""
    a = Answer(conclusion="澄清？", clarification="澄清？")
    assert _route_after_understand({"answer": a}, clarification=False) == "explore"
    assert _route_after_understand({"answer": None}, clarification=True) == "explore"
