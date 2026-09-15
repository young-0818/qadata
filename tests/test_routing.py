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


# ── M5 票 05：指标层路由（纯函数，仿既有 _route_after_* 先例）──────────

from qadata.graph.build import (
    _route_after_metric_match,
    _route_after_understand,
)


def test_understand_switch_off_skips_metric_layer():
    """总开关 False：understand 之后与现状逐行为一致（metric_match 不进）。"""
    assert _route_after_understand({"intent": None}, metric_layer=False) == "explore"


def test_understand_switch_on_enters_metric_match():
    assert _route_after_understand({"intent": None}, metric_layer=True) == "metric_match"


def test_metric_match_hit_goes_execute():
    assert _route_after_metric_match({"matched_metric": "loan_count"}) == "execute"


def test_metric_match_miss_or_skip_goes_explore():
    assert _route_after_metric_match({"matched_metric": None}) == "explore"
    assert _route_after_metric_match({}) == "explore"  # 键未写入（开关关）同判兜底


def test_template_exec_failure_downgrades_to_explore():
    """命中路径执行失败 → 降级兜底（不经 generate——那时还没有 schema）。"""
    s = {"result": None, "matched_metric": "m", "attempts": [SqlAttempt(sql="t", error="e")]}
    assert _route_after_execute(s, budget=3) == "explore"


def test_template_failure_downgrade_also_budget_gated():
    """降级不赠额外预算：账本耗尽时诚实兜底（与常规环同闸，票 05 code-review 收紧）。"""
    s = {"result": None, "matched_metric": "m", "attempts": [SqlAttempt(sql="t", error="e")]}
    assert _route_after_execute(s, budget=1) == "respond"


def test_template_success_still_goes_verify():
    s = {"result": _ok_result(), "matched_metric": "m",
         "attempts": [SqlAttempt(sql="t", row_count=1)]}
    assert _route_after_execute(s, budget=3) == "verify"


def test_fallback_failure_after_downgrade_uses_existing_loop():
    """降级后 matched_metric 已被 explore 清 None：后续失败回既有重试环，不再降级。"""
    s = {"result": None, "matched_metric": None, "attempts": [SqlAttempt(sql="x", error="e")]}
    assert _route_after_execute(s, budget=3) == "generate"


def test_metric_verify_suspicious_with_budget_downgrades():
    """命中但校验可疑 → 经 explore 换兜底（进重试环需 schema，直接 generate 是空上下文）。"""
    s = {"verify_note": "结果为空", "matched_metric": "m",
         "attempts": [SqlAttempt(sql="t", row_count=0)]}
    assert _route_after_verify(s, budget=3) == "explore"


def test_metric_verify_suspicious_exhausted_answers_annotated():
    """预算耗尽不降级：数据真实，带标注作答（与兜底路径同形态）。"""
    s = {"verify_note": "结果为空", "matched_metric": "m",
         "attempts": [SqlAttempt(sql="t", row_count=0)] * 3}
    assert _route_after_verify(s, budget=3) == "respond"


def test_metric_path_passes_verify_to_respond():
    s = {"verify_note": None, "matched_metric": "m",
         "attempts": [SqlAttempt(sql="t", row_count=1)]}
    assert _route_after_verify(s, budget=3) == "respond"


# ── M8 票 03：澄清出口首判（纯函数直测；END 分支随开关才进 map 见 build_graph）──


def test_clarification_flag_on_answer_present_goes_end():
    """开态且 understand 出口已落 answer（澄清轮）→ 首判直达 END——
    先于指标层判定（metric_layer 同开也走 END，answer 优先）。"""
    a = Answer(conclusion="澄清？", clarification="澄清？")
    assert _route_after_understand({"answer": a}, metric_layer=False,
                                   clarification=True) == "END"
    assert _route_after_understand({"answer": a}, metric_layer=True,
                                   clarification=True) == "END"


def test_clarification_flag_off_never_ends():
    """关态逐行为一致：answer 键即便在（不可能，双保险）也不判 END；
    开态但无 answer（常规轮）→ 与关态同路由。"""
    a = Answer(conclusion="澄清？", clarification="澄清？")
    assert _route_after_understand({"answer": a}, metric_layer=False,
                                   clarification=False) == "explore"
    assert _route_after_understand({"answer": a}, metric_layer=True,
                                   clarification=False) == "metric_match"
    assert _route_after_understand({"answer": None}, metric_layer=False,
                                   clarification=True) == "explore"


def test_route_after_understand_default_param_backward():
    """第三参缺省＝旧调用面行为不动（既有二参直测/接线零破坏）。"""
    assert _route_after_understand({"answer": Answer(conclusion="x")}, True) == "metric_match"
