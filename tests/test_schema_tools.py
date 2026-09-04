import json

from qadata.tools.schema import (
    build_schema_context,
    get_schema,
    list_tables,
    sample_rows,
)


class FakeLLM:
    """只测接口约定：invoke(str) 返回带 .content 的对象；记录调用次数以证实分支被执行。"""

    def __init__(self, content):
        self.content = content
        self.calls = 0

    def invoke(self, _prompt):
        self.calls += 1
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


def test_build_schema_context_large_db_uses_llm(fixture_conn):
    # 显式传 max_chars 触发 LLM 分支（不 monkeypatch 常量：函数默认值在 def 时绑定，patch 不生效）
    fake = FakeLLM("students, scores")
    ctx = build_schema_context(fixture_conn, "任意问题", llm=fake, max_chars=10)
    assert fake.calls == 1  # LLM 确实被调用
    assert "CREATE TABLE students" in ctx and "CREATE TABLE scores" in ctx


def test_build_schema_context_llm_garbage_falls_back(fixture_conn):
    fake = FakeLLM("这些表不存在的回答")
    ctx = build_schema_context(fixture_conn, "任意问题", llm=fake, max_chars=10)
    assert fake.calls == 1
    # 解析失败 → 回退全量：两张表都在
    assert "CREATE TABLE students" in ctx and "CREATE TABLE scores" in ctx


def test_llm_selection_result_is_used(fixture_conn):
    """LLM 只选 students 时，scores 不得出现在上下文中——证明选表结果真正被使用。"""
    fake = FakeLLM("students")
    ctx = build_schema_context(fixture_conn, "任意问题", llm=fake, max_chars=10)
    assert fake.calls == 1
    assert "CREATE TABLE students" in ctx and "CREATE TABLE scores" not in ctx


def test_llm_selection_call_traced(fixture_conn, tmp_path):
    """M1 观测盲区修复：explore 的选表 LLM 调用必须进 tracing。"""
    from qadata.llm.tracing import TraceLogger
    from tests.fakes import ScriptedLLM

    log = TraceLogger(tmp_path / "t.jsonl")
    build_schema_context(fixture_conn, "任意问题", llm=ScriptedLLM(["students"]),
                         max_chars=10, tracer=log)
    rec = json.loads((tmp_path / "t.jsonl").read_text(encoding="utf-8"))
    assert rec["node"] == "explore"


def test_list_tables_includes_views(fixture_db):
    import sqlite3

    conn = sqlite3.connect(fixture_db)
    conn.execute("CREATE VIEW adults AS SELECT id, name FROM students WHERE grade >= 3")
    conn.commit()
    conn.close()
    conn = sqlite3.connect(f"file:{fixture_db}?mode=ro", uri=True)
    try:
        assert "adults" in list_tables(conn)
        assert "CREATE VIEW adults" in get_schema(conn, "adults")
    finally:
        conn.close()


def test_database_description_in_context(fixture_conn, fixture_db):
    import csv
    from pathlib import Path

    desc_dir = Path(fixture_db).parent / "database_description"
    desc_dir.mkdir(exist_ok=True)
    with (desc_dir / "students.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["column_name", "column_description", "data_format", "value_description"])
        w.writerow(["name", "学生姓名", "", ""])
        w.writerow(["grade", "年级", "", "1-6"])
    ctx = build_schema_context(fixture_conn, "成绩最好的学生是谁", llm=None, db_path=fixture_db)
    assert "表 students 列注释" in ctx
    assert "学生姓名" in ctx and "1-6" in ctx


def test_database_description_missing_silently_skipped(fixture_conn, fixture_db):
    ctx = build_schema_context(fixture_conn, "q", llm=None, db_path=fixture_db)
    assert "列注释" not in ctx


def test_database_description_only_selected_tables(fixture_conn, fixture_db):
    """选表路径：只进选中表的注释（token 纪律）。"""
    import csv
    from pathlib import Path

    desc_dir = Path(fixture_db).parent / "database_description"
    desc_dir.mkdir(exist_ok=True)
    for table in ("students", "scores"):
        with (desc_dir / f"{table}.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["column_name", "column_description", "data_format", "value_description"])
            w.writerow(["name", f"{table} 的注释", "", ""])
    fake = FakeLLM("students")
    ctx = build_schema_context(fixture_conn, "任意问题", llm=fake, max_chars=10, db_path=fixture_db)
    assert "students 的注释" in ctx and "scores 的注释" not in ctx
