"""M7 票 03 专测：`on_event` 节点级进度帧（graph 层）；M8 票 06 喂厚。

帧三型（票 06）：start 帧＝node/attempt/status 三字段不动；结果帧追加
ok/duration_ms/tokens_in/tokens_out（chart 可选字段先例——消费者可忽略，
末帧 answer 契约零动）；tool 子事件帧＝node/kind/tool/ok/duration_ms
（explore 内 list_tables/get_schema/[select_tables]/[value_samples]、
execute 内 execute_sql，on_event 沿参透传 tools 层）。
钉四件事：①缺省 None 时与现状逐行为一致（CLI/eval 调用面零改动——调用面源码
出现 on_event 即红，tools 层不传即零发）；②帧形状三型钉死；③假模型下自纠错
全编——失败→重试→成功、失败红点子事件、提取失败→转重试、预算耗尽→如实报失败
的完整帧序列逐帧钉死（duration_ms 是计时量：钉存在不钉值）；④tokens 经 timed_invoke
sink 逐调用累计进结果帧（假模型无 usage＝恒 0，真值形态单独钉）。
status 文案后端单源（票面"中文修复建议进重试"即此）。
"""
import inspect

from qadata.config import Settings
from qadata.graph.build import run_question
from tests.fakes import ScriptedLLM

_S = Settings(api_key="", base_url="", model="test-model", retry_budget=3)
_START_KEYS = {"node", "attempt", "status"}
_RESULT_KEYS = _START_KEYS | {"ok", "duration_ms", "tokens_in", "tokens_out"}
_TOOL_KEYS = {"node", "kind", "tool", "ok", "duration_ms"}

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


def _fr(node: str, attempt: int, status: str, *, ok: bool = True) -> dict:
    """结果帧期望形（tokens 在假模型下恒 0——真值形态由 test_token_sink 单独钉）。"""
    return {"node": node, "attempt": attempt, "status": status, "ok": ok,
            "tokens_in": 0, "tokens_out": 0}


def _tool(node: str, tool: str, *, ok: bool = True) -> dict:
    return {"node": node, "kind": "tool", "tool": tool, "ok": ok}


def _strip(frames: list[dict]) -> list[dict]:
    """剥 duration_ms 后返回副本（序列钉防计时抖动；start 帧本无此键）。
    存在性与类型由 test_frame_shape_three_kinds 的键集钉负责，不在此重复。"""
    out = []
    for f in frames:
        g = dict(f)
        g.pop("duration_ms", None)
        out.append(g)
    return out


# ── 调用面零改动／关态一致 ──────────────────────────────────────────


def test_run_question_signature_tail_default_none():
    """末位可选参纪律：on_event（票 03）与 session_context（票 05）、thread_id＋checkpointer
    （M8 票 03 改判＝经典 HITL）都在 evidence 之后以缺省 None 的末位参数追加——
    既有位置参/关键字调用面零改动，CLI/eval 不受影响。
    （本断言原钉 on_event 为末位；票 05 合法追加后钉"末两位皆缺省 None"；
    HITL 改判追加后钉"末四位皆缺省 None"；M9 票 01 观测出口追加后钉"末五位"；
    M9 票 06 召回回调追加后钉"末六位"；M10 票 01 表卡粗召回沿同一族追加后钉"末七位"；
    M10 票 03 值链查询调沿同一族追加后钉"末八位"；M10 票 05 字典召回回调沿同一族
    追加后钉"末九位"。）"""
    params = list(inspect.signature(run_question).parameters.values())
    for tail, want in ((-1, "knowledge_recall"), (-2, "value_link"), (-3, "table_recall"),
                       (-4, "recall"), (-5, "obs"), (-6, "checkpointer"),
                       (-7, "thread_id"), (-8, "session_context"), (-9, "on_event")):
        assert params[tail].name == want and params[tail].default is None


def test_cli_and_eval_call_sites_untouched():
    """票面"CLI/eval 调用面零改动"钉死：两个调用方源码里不得出现进度流/会话/HITL 参数
    ——澄清在 CLI/评测侧恒走直 END 形态（无 key 可续，暂停无意义），HITL 属 web 专属。"""
    import qadata.cli.main as cli_main
    import qadata.eval.bird as eval_bird

    for name in ("on_event", "session_context", "thread_id", "checkpointer",
                 "resume_question"):
        assert name not in inspect.getsource(cli_main), name
        assert name not in inspect.getsource(eval_bird), name  # eval 带 skip_respond=True 的路径同样不传


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


# ── 帧形状（票 06 三型）────────────────────────────────────────────


def test_frame_shape_three_kinds(fixture_db):
    frames, _, _ = _collect(fixture_db, _RETRY_SCRIPT)
    nodes = {"understand", "metric_match", "explore", "generate", "execute",
             "verify", "respond"}
    saw_tool = saw_result = False
    for f in frames:
        assert f["node"] in nodes
        if f.get("kind") == "tool":
            saw_tool = True
            assert set(f) == _TOOL_KEYS, f
            assert isinstance(f["ok"], bool)
        elif f["status"] == "start":
            assert isinstance(f["status"], str) and f["status"]
            assert set(f) == _START_KEYS, f  # start 帧三字段不动（票 03 契约）
        else:
            saw_result = True
            assert isinstance(f["status"], str) and f["status"]
            assert set(f) == _RESULT_KEYS, f
            assert isinstance(f["attempt"], int) and f["attempt"] >= 0
            assert isinstance(f["ok"], bool)
            assert isinstance(f["tokens_in"], int) and isinstance(f["tokens_out"], int)
    assert saw_tool, "票 06：tool 子事件帧必须真实出现（防全序列退化为两型）"
    assert saw_result
    assert not any(f["node"] == "metric_match" for f in frames)  # 指标层默认关＝节点不进


# ── 完整序列钉死（票面：失败→重试→成功＋tool 子事件与失败红点）──────


def test_fail_retry_success_full_sequence(fixture_db):
    frames, llm, answer = _collect(fixture_db, _RETRY_SCRIPT)
    assert _strip(frames) == [
        {"node": "understand", "attempt": 0, "status": "start"},
        _fr("understand", 0, "解析失败，按原问题作答"),
        {"node": "explore", "attempt": 0, "status": "start"},
        _tool("explore", "list_tables"),
        _tool("explore", "get_schema"),
        _fr("explore", 0, "取到 Schema"),
        {"node": "generate", "attempt": 0, "status": "start"},
        _fr("generate", 0, "生成 SQL"),
        {"node": "execute", "attempt": 0, "status": "start"},
        _tool("execute", "execute_sql", ok=False),  # 失败红点（截图语义）
        _fr("execute", 1, _FAIL_HINT, ok=False),
        {"node": "generate", "attempt": 1, "status": "start"},
        _fr("generate", 1, "生成 SQL"),
        {"node": "execute", "attempt": 1, "status": "start"},
        _tool("execute", "execute_sql"),
        _fr("execute", 2, "执行成功：1 行"),
        {"node": "verify", "attempt": 2, "status": "start"},
        _fr("verify", 2, "校验通过"),
        {"node": "respond", "attempt": 2, "status": "start"},
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
    statuses = [(f["node"], f["status"], f.get("ok"))
                for f in frames if f.get("kind") != "tool"]
    assert ("generate", "提取失败：回复中未找到合法 SQL：'我答不上来'", False) in statuses
    assert ("execute", "无可执行 SQL，转重试", False) in statuses
    # 提取失败没走到执行——两回合合计只有 list_tables/get_schema 与一次成功的 execute_sql
    tools = [(f["node"], f["tool"], f["ok"]) for f in frames if f.get("kind") == "tool"]
    assert tools.count(("execute", "execute_sql", True)) == 1
    assert ("execute", "execute_sql", False) not in tools
    assert statuses[-1] == ("respond", "作答完成", True)
    assert answer.failed is False


def test_budget_exhausted_ends_honest_failure(fixture_db):
    frames, _, answer = _collect(fixture_db, ["改写", _BAD, _BAD, _BAD])
    assert _strip(frames)[:2] == [{"node": "understand", "attempt": 0, "status": "start"},
                                  _fr("understand", 0, "解析失败，按原问题作答")]
    assert [f["node"] for f in frames[-2:]] == ["respond", "respond"]
    assert frames[-1]["status"] == "如实报失败" and frames[-1]["attempt"] == 3
    assert frames[-1]["ok"] is False  # 失败收口红点
    assert sum(1 for f in frames if f.get("status") == _FAIL_HINT) == 3
    assert sum(1 for f in frames
               if f.get("kind") == "tool" and f["tool"] == "execute_sql"
               and f["ok"] is False) == 3  # 三次执行全败＝三枚红点
    assert not any(f["node"] == "verify" for f in frames)  # 执行全败→路由不进校验（帧面同证）
    assert answer.failed is True


# ── tokens 进帧（票 06 费用卡的数据源，sink 沿 timed_invoke 透传）────


def test_token_sink_accumulates(fixture_db):
    """纪律 #5 正统路：ScriptedLLM 的可选 usage 参（双轴评审整改——不立平行假模型）。"""
    u = {"input_tokens": 100, "output_tokens": 5, "total_tokens": 105}
    frames: list[dict] = []
    llm = ScriptedLLM(['{"question":"查询成绩"}', _GOOD, "Bob 88"], usage=u)
    run_question(fixture_db, "谁成绩最好", llm=llm, settings=_S, on_event=frames.append)
    by = {(f["node"], f["status"]): f for f in frames if f.get("kind") != "tool"}
    assert (by[("understand", "理解完成")]["tokens_in"],
            by[("understand", "理解完成")]["tokens_out"]) == (100, 5)
    assert by[("generate", "生成 SQL")]["tokens_in"] == 100
    assert by[("respond", "作答完成")]["tokens_out"] == 5
    assert by[("verify", "校验通过")]["tokens_in"] == 0  # 纯码节点零调用＝0，不假装有
    assert llm.calls == 3


# ── 与评测开关正交（侦察笔记：eval 调用带 skip_respond=True）────────


def test_skip_respond_orthogonal_with_on_event(fixture_db):
    """skip_respond 语义不因 on_event 变：仍省 respond 的 LLM 调用，帧照常收口。"""
    frames, llm, answer = _collect(fixture_db, ["改写", _GOOD], skip_respond=True)
    assert llm.calls == 2  # understand＋generate，respond 成功路径不调模型
    tail = dict(frames[-1])
    assert isinstance(tail.pop("duration_ms"), int)  # 计时量只钉在场
    assert tail == _fr("respond", 1, "作答完成")
    assert "评测模式" in answer.conclusion
