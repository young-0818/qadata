from qadata.graph.build import _route_after_execute, _route_after_verify
from qadata.types import QueryResult, SqlAttempt


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
