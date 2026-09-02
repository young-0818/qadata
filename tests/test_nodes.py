import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from qadata.graph.nodes import extract_sql, format_rows, make_nodes
from qadata.types import QueryResult


FAKE_DB = None  # 节点测试不碰真库；execute 走 fixture_db


def test_extract_sql_strips_fences():
    assert extract_sql("```sql\nSELECT 1;\n```") == "SELECT 1"
    assert extract_sql("SELECT 2") == "SELECT 2"
    with pytest.raises(ValueError, match="SQL"):
        extract_sql("抱歉，我不会")


def test_format_rows_pipe_table():
    r = QueryResult(columns=["name", "score"], rows=[("A", 1.0)], row_count=1, truncated=False, elapsed_ms=1)
    t = format_rows(r)
    assert "name" in t and "A" in t and "1.0" in t


def test_nodes_happy_path(fixture_db, tmp_path):
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
