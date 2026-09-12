"""M7 票 03 专测：`on_event` 节点级进度帧（graph 层）。

钉三件事：①缺省 None 时与现状逐行为一致（CLI/eval 调用面零改动——调用面源码
出现 on_event 即红）；②帧形状只有 node/attempt/status 三字段（spec 响应契约）；
③假模型下自纠错全编——失败→重试→成功、提取失败→转重试、预算耗尽→如实报失败
的完整帧序列逐帧钉死。status 文案后端单源（票面"中文修复建议进重试"即此）。
"""
import inspect

from qadata.config import Settings
from qadata.graph.build import run_question
from tests.fakes import ScriptedLLM

_S = Settings(api_key="", base_url="", model="test-model", retry_budget=3)
_FRAME_KEYS = {"node", "attempt", "status"}

_GOOD = "SELECT name FROM students WHERE id = 2"
_BAD = "SELECT nope FROM students"
_FAIL_HINT = "执行失败：no such column: nope；修复建议：核对列名拼写，对照 Schema"

_RETRY_SCRIPT = ["改写", _BAD, _GOOD, "Bob 数学 88 分"]


def _collect(db, script, question="谁成绩最好", **kw):
    frames: list[dict] = []
    llm = ScriptedLLM(script)
    answer = run_question(db, question, llm=llm, settings=_S,
                          on_event=frames.append, **kw)
    return frames, llm, answer


def _fr(node: str, attempt: int, status: str) -> dict:
    return {"node": node, "attempt": attempt, "status": status}


# ── 调用面零改动／关态一致 ──────────────────────────────────────────


def test_run_question_signature_tail_default_none():
    """on_event 必须是末位可选参（缺省 None）——位置不动则 CLI/eval 现有调用零改动。"""
    params = list(inspect.signature(run_question).parameters.values())
    assert params[-1].name == "on_event" and params[-1].default is None


def test_cli_and_eval_call_sites_untouched():
    """票面"CLI/eval 调用面零改动"钉死：两个调用方源码里不得出现 on_event。"""
    import qadata.cli.main as cli_main
    import qadata.eval.bird as eval_bird

    assert "on_event" not in inspect.getsource(cli_main)
    assert "on_event" not in inspect.getsource(eval_bird)  # eval 带 skip_respond=True 的路径同样不传


def test_off_state_answer_identical(fixture_db):
    """关态逐行为一致：同一脚本双跑（不带/带 on_event），答案逐字段与调用数相同。"""
    plain = ScriptedLLM(_RETRY_SCRIPT)
    ans_plain = run_question(fixture_db, "谁成绩最好", llm=plain, settings=_S)
    frames, scripted, ans_on = _collect(fixture_db, _RETRY_SCRIPT)
    assert scripted.calls == plain.calls == 4
    assert frames  # 对照组真收到了帧（防两跑都空转的假一致）
    for f in ("conclusion", "sql", "failed", "error_summary", "path",
              "metric_name", "template_fell_back"):
        assert getattr(ans_on, f) == getattr(ans_plain, f), f
    r1, r2 = ans_plain.result, ans_on.result
    assert (r1.columns, r1.rows, r1.row_count, r1.truncated) == \
        (r2.columns, r2.rows, r2.row_count, r2.truncated)  # elapsed_ms 是计时量，不比


# ── 帧形状 ──────────────────────────────────────────────────────────


def test_frame_shape_three_fields_only(fixture_db):
    frames, _, _ = _collect(fixture_db, _RETRY_SCRIPT)
    nodes = {"understand", "metric_match", "explore", "generate", "execute",
             "verify", "respond"}
    for f in frames:
        assert set(f) == _FRAME_KEYS, f
        assert isinstance(f["attempt"], int) and f["attempt"] >= 0
        assert f["node"] in nodes
        assert isinstance(f["status"], str) and f["status"]
    assert not any(f["node"] == "metric_match" for f in frames)  # 指标层默认关＝节点不进


# ── 完整序列钉死（票面：失败→重试→成功形态）────────────────────────


def test_fail_retry_success_full_sequence(fixture_db):
    frames, llm, answer = _collect(fixture_db, _RETRY_SCRIPT)
    assert frames == [
        _fr("understand", 0, "start"),
        _fr("understand", 0, "解析失败，按原问题作答"),
        _fr("explore", 0, "start"),
        _fr("explore", 0, "取到 Schema"),
        _fr("generate", 0, "start"),
        _fr("generate", 0, "生成 SQL"),
        _fr("execute", 0, "start"),
        _fr("execute", 1, _FAIL_HINT),
        _fr("generate", 1, "start"),
        _fr("generate", 1, "生成 SQL"),
        _fr("execute", 1, "start"),
        _fr("execute", 2, "执行成功：1 行"),
        _fr("verify", 2, "start"),
        _fr("verify", 2, "校验通过"),
        _fr("respond", 2, "start"),
        _fr("respond", 2, "作答完成"),
    ]
    assert answer.failed is False and llm.calls == 4


def test_understand_success_branch(fixture_db):
    """意图 JSON 正常 → understand 出"理解完成"帧（另一分支在 full_sequence 已钉）。"""
    frames, _, _ = _collect(fixture_db, ['{"question":"查询成绩"}', _GOOD, "Bob 88"])
    assert frames[1]["status"] == "理解完成"
    assert frames[1]["node"] == "understand"


def test_extract_failure_then_retry_frames(fixture_db):
    frames, _, answer = _collect(
        fixture_db, ["改写", "我答不上来", "```sql\n" + _GOOD + "\n```", "Bob"])
    statuses = [(f["node"], f["status"]) for f in frames]
    assert ("generate", "提取失败：回复中未找到合法 SQL：'我答不上来'") in statuses
    assert ("execute", "无可执行 SQL，转重试") in statuses
    assert statuses[-1] == ("respond", "作答完成")
    assert answer.failed is False


def test_budget_exhausted_ends_honest_failure(fixture_db):
    frames, _, answer = _collect(fixture_db, ["改写", _BAD, _BAD, _BAD])
    assert frames[:2] == [_fr("understand", 0, "start"),
                          _fr("understand", 0, "解析失败，按原问题作答")]
    assert [f["node"] for f in frames[-2:]] == ["respond", "respond"]
    assert frames[-1]["status"] == "如实报失败" and frames[-1]["attempt"] == 3
    assert sum(1 for f in frames if f["status"] == _FAIL_HINT) == 3
    assert not any(f["node"] == "verify" for f in frames)  # 执行全败→路由不进校验（帧面同证）
    assert answer.failed is True


# ── 与评测开关正交（侦察笔记：eval 调用带 skip_respond=True）────────


def test_skip_respond_orthogonal_with_on_event(fixture_db):
    """skip_respond 语义不因 on_event 变：仍省 respond 的 LLM 调用，帧照常收口。"""
    frames, llm, answer = _collect(fixture_db, ["改写", _GOOD], skip_respond=True)
    assert llm.calls == 2  # understand＋generate，respond 成功路径不调模型
    assert frames[-1] == _fr("respond", 1, "作答完成")
    assert "评测模式" in answer.conclusion
