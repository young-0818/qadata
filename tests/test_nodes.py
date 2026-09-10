import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from qadata.graph.nodes import extract_sql, format_rows, make_nodes
from qadata.types import QueryResult
from tests.conftest import RecorderLLM
from tests.fakes import ScriptedLLM


def test_extract_sql_strips_fences():
    assert extract_sql("```sql\nSELECT 1;\n```") == "SELECT 1"
    assert extract_sql("SELECT 2") == "SELECT 2"
    with pytest.raises(ValueError, match="SQL"):
        extract_sql("抱歉，我不会")


def test_format_rows_pipe_table():
    r = QueryResult(columns=["name", "score"], rows=[("A", 1.0)], row_count=1, truncated=False, elapsed_ms=1)
    t = format_rows(r)
    assert "name" in t and "A" in t and "1.0" in t


def test_nodes_pass_limiter_to_timed_invoke(monkeypatch):
    """限速器透传：make_nodes 注入的 limiter 必须进全部 timed_invoke 调用。"""
    captured = {}

    def fake_timed_invoke(llm, prompt, node, tracer, limiter=None):
        captured[node] = limiter
        return "ok"

    monkeypatch.setattr("qadata.graph.nodes.timed_invoke", fake_timed_invoke)
    sentinel = object()
    nodes = make_nodes(None, None, limiter=sentinel)
    nodes["understand"]({"question": "q"})
    assert captured["understand"] is sentinel


def test_skip_respond_placeholder_conclusion_without_llm(fixture_db):
    """评测模式 --skip-respond：成功路径不调 LLM（ScriptedLLM 空脚本零容忍），
    占位结论入 Answer，sql/result 原样保留（判分只读 sql）。"""
    from tests.fakes import ScriptedLLM

    llm = ScriptedLLM(["每个学生的平均成绩是多少", "SELECT name FROM students WHERE id = 1"])
    nodes = make_nodes(llm, skip_respond=True)
    state = {"db_path": fixture_db, "question": "谁成绩最好"}
    state.update(nodes["understand"](state))
    state.update(nodes["explore"](state))
    state.update(nodes["generate"](state))
    state.update(nodes["execute"](state))
    state.update(nodes["verify"](state))
    state.update(nodes["respond"](state))
    ans = state["answer"]
    assert llm.calls == 2  # understand + generate；respond 未调（超脚本即炸）
    assert ans.failed is False
    assert ans.sql == "SELECT name FROM students WHERE id = 1"
    assert ans.result.rows == [("Alice",)]
    assert "跳过结论生成" in ans.conclusion


def test_skip_respond_failure_path_stays_honest(fixture_db):
    """失败路径（执行失败耗尽）仍走确定性诚实汇报，skip 只作用于成功路径。"""
    from tests.fakes import ScriptedLLM

    llm = ScriptedLLM(["q", "SELECT nope FROM students"])
    nodes = make_nodes(llm, skip_respond=True)
    state = {"db_path": fixture_db, "question": "谁成绩最好"}
    state.update(nodes["understand"](state))
    state.update(nodes["explore"](state))
    state.update(nodes["generate"](state))
    state.update(nodes["execute"](state))
    state.update(nodes["verify"](state))
    state.update(nodes["respond"](state))
    ans = state["answer"]
    assert llm.calls == 2  # 失败汇报本就不调 LLM（永不编造）
    assert ans.failed is True
    assert "未能完成查询" in ans.conclusion and "nope" in ans.conclusion


def test_nodes_happy_path(fixture_db):
    llm = FakeListChatModel(
        responses=["每个学生的平均成绩是多少", "SELECT name FROM students WHERE id = 1", "Alice 的成绩最好"]
    )
    nodes = make_nodes(llm)
    state = {"db_path": fixture_db, "question": "谁成绩最好"}
    state.update(nodes["understand"](state))
    assert state["question"] == "每个学生的平均成绩是多少"
    state.update(nodes["explore"](state))
    assert "CREATE TABLE" in state["db_schema"]
    state.update(nodes["generate"](state))
    assert state["current_sql"] == "SELECT name FROM students WHERE id = 1"
    state.update(nodes["execute"](state))
    assert state["result"].rows == [("Alice",)]
    state.update(nodes["respond"](state))
    ans = state["answer"]
    assert ans.failed is False
    # 票 07：三节组装——LLM 文本进【结论】节，数据依据确定性派生
    assert ans.conclusion.startswith("【结论】Alice 的成绩最好")
    assert "所用表：students" in ans.conclusion
    assert ans.sql == "SELECT name FROM students WHERE id = 1"


def test_nodes_no_sql_fails_honestly(fixture_db):
    llm = FakeListChatModel(responses=["改写后的问题", "对不起，我回答不了"])
    nodes = make_nodes(llm)
    state = {"db_path": fixture_db, "question": "谁成绩最好"}
    state.update(nodes["understand"](state))
    state.update(nodes["explore"](state))
    state.update(nodes["generate"](state))  # 提取不到 SQL
    assert state.get("current_sql") is None and state.get("last_error")
    state.update(nodes["execute"](state))  # 无 SQL → no-op
    state.update(nodes["respond"](state))
    ans = state["answer"]
    assert ans.failed is True and ans.sql is None
    assert "未能" in ans.conclusion  # 永不编造


def test_nodes_sql_error_recorded(fixture_db):
    llm = FakeListChatModel(responses=["q", "SELECT nope FROM students", "r"])
    nodes = make_nodes(llm)
    state = {"db_path": fixture_db, "question": "q"}
    for name in ("understand", "explore", "generate"):
        state.update(nodes[name](state))
    state.update(nodes["execute"](state))
    assert state["result"] is None and "no such column" in state["last_error"]
    assert state["attempts"] and state["attempts"][-1].error  # 失败历史
    state.update(nodes["respond"](state))
    assert state["answer"].failed is True


def test_understand_keeps_original_question():
    from qadata.graph.nodes import make_nodes
    from tests.fakes import ScriptedLLM

    nodes = make_nodes(ScriptedLLM(["改写后的问题"]))
    out = nodes["understand"]({"question": "原始问题"})
    # M5 票 02 起 understand 多返回 intent 键（纯文本＝解析失败回退态 None，同 test_intent.py）
    assert out == {"original_question": "原始问题", "question": "改写后的问题", "intent": None}


def test_generate_failure_records_attempt(fixture_db):
    """§11.1：generate 提取失败也写 SqlAttempt——len(attempts) 才是完整的预算账本。"""
    from qadata.graph.nodes import make_nodes
    from tests.fakes import ScriptedLLM

    nodes = make_nodes(ScriptedLLM(["对不起，我不会写 SQL"]))
    state = {"db_path": fixture_db, "question": "q", "db_schema": "CREATE TABLE students (id INTEGER);"}
    out = nodes["generate"](state)
    assert out["current_sql"] is None
    assert len(out["attempts"]) == 1 and out["attempts"][0].sql == ""
    assert "未找到合法 SQL" in out["attempts"][0].error


def test_respond_notes_truncation():
    """截断提示：truncated=True 时 respond prompt 必须带截断说明，且预览行数为 PREVIEW_ROWS。"""
    res = QueryResult(
        columns=["name"],
        rows=[(f"r{i}",) for i in range(12)],
        row_count=12,
        truncated=True,
        elapsed_ms=1,
    )
    recorder = RecorderLLM(["结论"])
    nodes = make_nodes(recorder)
    state = {
        "db_path": "unused",
        "question": "q",
        "current_sql": "SELECT name FROM students",
        "result": res,
        "last_error": None,
    }
    out = nodes["respond"](state)
    assert out["answer"].failed is False
    prompt = recorder.prompts[0]
    assert "结果已截断" in prompt  # 提示模型如实措辞，勿把截断当全量
    assert "前 10 行" in prompt  # n 由 PREVIEW_ROWS 派生


# ── M5 票 07：E1 三节组装（respond 节点）───────────────────────────


def _ok_result(rows=(("Alice",),), row_count=None, truncated=False):
    return QueryResult(columns=["name"], rows=list(rows),
                       row_count=row_count if row_count is not None else len(rows),
                       truncated=truncated, elapsed_ms=1)


def _respond_state(**over):
    state = {"db_path": "unused", "question": "q",
             "current_sql": "SELECT name FROM students WHERE id = 1",
             "result": _ok_result(), "last_error": None}
    state.update(over)
    return state


def test_respond_success_minimal_three_sections_without_idle():
    """兜底题无口径来源、无标注：三节中该省的省——口径/校验两节整体不出现（不空转）。"""
    llm = ScriptedLLM(["Alice 最好"])
    out = make_nodes(llm)["respond"](_respond_state())
    c = out["answer"].conclusion
    assert c.startswith("【结论】Alice 最好")
    assert "【数据依据】共取到 1 行（全部列示）；所用表：students" in c
    assert "【口径说明】" not in c and "【校验标注】" not in c


def test_respond_fallback_caliber_cites_evidence_terms():
    """兜底路径口径说明＝evidence 命中项（载体 A evidence_terms 原样摘录，零 prompt）。"""
    llm = ScriptedLLM(["Alice 最好"])
    state = _respond_state(intent={"evidence_terms": ["全名 = first_name, last_name"]})
    out = make_nodes(llm)["respond"](state)
    c = out["answer"].conclusion
    assert "【口径说明】" in c and "全名 = first_name, last_name" in c
    assert "【校验标注】" not in c


def test_respond_fallback_caliber_omitted_when_terms_empty():
    """宁空勿造的另一面：intent 在但 evidence_terms 空/null → 口径节省略。"""
    for intent in ({"evidence_terms": []}, {"evidence_terms": None}, {"metric_mention": "x"}, None):
        llm = ScriptedLLM(["r"])
        out = make_nodes(llm)["respond"](_respond_state(intent=intent))
        assert "【口径说明】" not in out["answer"].conclusion


def test_respond_truncation_and_verify_notes_merged_into_check_section():
    """截断标注与校验可疑并入【校验标注】节，逐条列、不另开新节（票面条款④）。"""
    res = _ok_result(rows=[(f"r{i}",) for i in range(12)], row_count=12, truncated=True)
    llm = ScriptedLLM(["见明细"])
    state = _respond_state(result=res, verify_note="结果为空")
    out = make_nodes(llm)["respond"](state)
    c = out["answer"].conclusion
    assert "【校验标注】" in c
    assert "- 该结果未通过自动校验：结果为空" in c
    assert "- 结果已截断" in c
    assert c.index("- 该结果未通过自动校验") < c.index("- 结果已截断")


def test_respond_failure_path_honest_sections_and_zero_llm():
    """失败态：respond 不调 LLM（ScriptedLLM 空脚本＝超脚本即炸），仍出三节诚实形态。"""
    from qadata.types import SqlAttempt

    llm = ScriptedLLM([])
    state = {"db_path": "unused", "question": "q", "current_sql": None, "result": None,
             "attempts": [SqlAttempt(sql="SELECT nope FROM students",
                                     error="no such column: nope")],
             "last_error": "no such column: nope"}
    out = make_nodes(llm)["respond"](state)
    ans = out["answer"]
    assert ans.failed is True
    c = ans.conclusion
    assert "【结论】未能完成查询（共尝试 1 次）" in c
    assert "【数据依据】无成功执行的查询，无可用结果集" in c


def test_respond_failure_path_carries_template_downgrade_note():
    """失败态校验标注接入模板降级原因（原样、零 LLM）。"""
    from qadata.types import SqlAttempt

    llm = ScriptedLLM([])
    state = {"db_path": "unused", "question": "q", "current_sql": None, "result": None,
             "attempts": [SqlAttempt(sql="SELECT 1", error="boom")],
             "last_error": "boom", "matched_metric": None,
             "metric_note": "指标模板「loan_count」执行失败：boom"}
    out = make_nodes(llm)["respond"](state)
    c = out["answer"].conclusion
    assert "【校验标注】" in c and "- 指标模板「loan_count」执行失败：boom" in c


def test_last_good_sql_derived_from_attempts():
    from qadata.graph.nodes import _last_good_sql
    from qadata.types import SqlAttempt

    attempts = [
        SqlAttempt(sql="", error="回复中未找到合法 SQL"),   # 提取失败：跳过
        SqlAttempt(sql="SELECT a", row_count=1),            # 成功
        SqlAttempt(sql="SELECT b", error="no such column"),  # 执行失败：跳过
        SqlAttempt(sql="", error="仍未提取到"),              # 提取失败：跳过
    ]
    assert _last_good_sql(attempts) == "SELECT a"
    assert _last_good_sql([SqlAttempt(sql="SELECT x", error="boom")]) is None
    assert _last_good_sql([]) is None


def test_respond_fallback_reexec_failure_degrades(fixture_db, monkeypatch):
    """回退是锦上添花：重执行失败必须降级为原诚实失败汇报，不连累本体。"""
    from qadata.graph.nodes import make_nodes
    from qadata.types import SqlAttempt
    from tests.fakes import ScriptedLLM

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr("qadata.graph.nodes.execute_sql", boom)
    nodes = make_nodes(ScriptedLLM([]))
    state = {
        "db_path": fixture_db, "question": "q", "current_sql": None, "result": None,
        "attempts": [SqlAttempt(sql="SELECT name FROM students", row_count=1),
                     SqlAttempt(sql="SELECT bad", error="no such column: bad")],
    }
    out = nodes["respond"](state)
    assert out["answer"].failed is True
    assert "共尝试 2 次" in out["answer"].conclusion
