import sqlite3

from qadata.tools.schema import build_schema_context, get_schema, list_tables, sample_rows


class FakeLLM:
    """只测接口约定：invoke(str) 返回带 .content 的对象。"""

    def __init__(self, content):
        self.content = content

    def invoke(self, _prompt):
        return self


def test_list_tables(fixture_conn):
    assert list_tables(fixture_conn) == ["scores", "students"]  # sqlite_master 按名字序


def test_get_schema_contains_ddl_and_columns(fixture_conn):
    ddl = get_schema(fixture_conn, "students")
    assert "CREATE TABLE students" in ddl
    for col in ("id", "name", "grade"):
        assert col in ddl


def test_sample_rows_readable(fixture_conn):
    text = sample_rows(fixture_conn, "students", n=2)
    assert "Alice" in text and "Bob" in text


def test_build_schema_context_small_db_full(fixture_conn):
    ctx = build_schema_context(fixture_conn, "成绩最好的学生是谁", llm=None)
    assert "CREATE TABLE students" in ctx and "CREATE TABLE scores" in ctx


def test_build_schema_context_large_db_uses_llm(fixture_conn, monkeypatch):
    monkeypatch.setattr("qadata.tools.schema.FULL_SCHEMA_LIMIT", 10)  # 强制走 LLM 分支
    fake = FakeLLM("students, scores")
    ctx = build_schema_context(fixture_conn, "任意问题", llm=fake)
    assert "CREATE TABLE students" in ctx and "CREATE TABLE scores" in ctx


def test_build_schema_context_llm_garbage_falls_back(fixture_conn, monkeypatch):
    monkeypatch.setattr("qadata.tools.schema.FULL_SCHEMA_LIMIT", 10)
    fake = FakeLLM("这些表不存在的回答")
    ctx = build_schema_context(fixture_conn, "任意问题", llm=fake)
    assert "CREATE TABLE" in ctx  # 解析失败 → 回退全量
