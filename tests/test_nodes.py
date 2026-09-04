import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from qadata.graph.nodes import extract_sql, format_rows, make_nodes
from qadata.types import QueryResult
from tests.conftest import RecorderLLM


def test_extract_sql_strips_fences():
    assert extract_sql("```sql\nSELECT 1;\n```") == "SELECT 1"
    assert extract_sql("SELECT 2") == "SELECT 2"
    with pytest.raises(ValueError, match="SQL"):
        extract_sql("抱歉，我不会")


def test_format_rows_pipe_table():
    r = QueryResult(columns=["name", "score"], rows=[("A", 1.0)], row_count=1, truncated=False, elapsed_ms=1)
    t = format_rows(r)
    assert "name" in t and "A" in t and "1.0" in t


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
    assert ans.conclusion == "Alice 的成绩最好"
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
    assert out == {"original_question": "原始问题", "question": "改写后的问题"}


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
