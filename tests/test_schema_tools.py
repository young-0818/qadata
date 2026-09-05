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


def test_database_description_non_utf8_does_not_crash(fixture_conn, fixture_db):
    """M3 回归：BIRD 部分 CSV 非 UTF-8（如 formula_1 的 0x96）。
    读取不得抛 UnicodeDecodeError 连累 explore（答案降为无注释，不炸题）。"""
    from pathlib import Path

    desc_dir = Path(fixture_db).parent / "database_description"
    desc_dir.mkdir(exist_ok=True)
    # 写入含 Windows-1252 字符（0x96）的非法 UTF-8 字节
    (desc_dir / "students.csv").write_bytes(
        b"column_name,column_description,data_format,value_description\n"
        b"name,\x96 en dash desc,,\n")
    ctx = build_schema_context(fixture_conn, "q", llm=None, db_path=fixture_db)
    assert "CREATE TABLE students" in ctx  # DDL 仍在，未崩


def _write_desc_csv(fixture_db, table, rows):
    """辅助：写 database_description/{table}.csv（BIRD 四列表头）。"""
    import csv
    from pathlib import Path

    desc_dir = Path(fixture_db).parent / "database_description"
    desc_dir.mkdir(exist_ok=True)
    with (desc_dir / f"{table}.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["column_name", "column_description", "data_format", "value_description"])
        for r in rows:
            w.writerow(r)


def test_values_from_official_description_take_priority(fixture_conn, fixture_db, monkeypatch):
    """官方 value_description 为主源：存在时不得触发 DISTINCT 兜底（spec §3.3）。"""
    _write_desc_csv(fixture_db, "students", [
        ["name", "学生姓名", "TEXT", "Alice | Bob"],
        ["grade", "年级", "INTEGER", "1-6"],
    ])

    def boom(db_path_, table, column):
        # 只禁 students 的兜底（scores 无 CSV，其文本列采样是合法路径）
        if table == "students":
            raise AssertionError("官方 value_description 存在时不得触发 DISTINCT 兜底")

    monkeypatch.setattr("qadata.tools.schema._sample_column_values", boom)
    ctx = build_schema_context(fixture_conn, "q", llm=None, db_path=fixture_db)
    assert "表 students 取值参考" in ctx
    assert "Alice | Bob" in ctx and "1-6" in ctx
    assert "学生姓名" in ctx  # M3 列注释段不回退


def test_distinct_fallback_samples_text_column(fixture_conn, fixture_db):
    """CSV 取值缺失的文本列 → DISTINCT 采样兜底；数值列不采样。"""
    _write_desc_csv(fixture_db, "students", [
        ["name", "学生姓名", "TEXT", ""],
        ["grade", "年级", "INTEGER", ""],
    ])
    ctx = build_schema_context(fixture_conn, "q", llm=None, db_path=fixture_db)
    assert "取值参考" in ctx
    assert "Alice" in ctx and "Bob" in ctx
    values_part = ctx.split("取值参考")[1]
    assert "- grade：" not in values_part  # 数值列（INTEGER）不采样


def test_sample_timeout_skips_silently(fixture_conn, fixture_db, monkeypatch):
    """兜底采样失败（含 5s 超时）静默跳过该列，不炸 schema 构建（spec §3.3）。"""
    _write_desc_csv(fixture_db, "students", [["name", "学生姓名", "TEXT", ""]])
    from qadata.types import SqlExecutionError

    def boom(*a, **k):
        raise SqlExecutionError("查询超时（>5.0 秒），已被沙箱中断")

    monkeypatch.setattr("qadata.tools.executor.execute_sql", boom)
    ctx = build_schema_context(fixture_conn, "q", llm=None, db_path=fixture_db)
    assert "CREATE TABLE students" in ctx  # 未炸
    assert "取值参考" not in ctx           # 该列被跳过、无取值段


def test_values_section_capped_per_table(fixture_conn, fixture_db):
    """每表取值段字符上限：超长截断且带「…」（token 纪律）。"""
    long_vals = " | ".join(f"值{i}" for i in range(2000))
    _write_desc_csv(fixture_db, "students", [["name", "", "TEXT", long_vals]])
    ctx = build_schema_context(fixture_conn, "q", llm=None, db_path=fixture_db)
    section = ctx.split("表 students 取值参考：")[1]
    assert "…" in section
    assert "值0" in section                    # 开头保留
    assert len(section) < len(long_vals) // 2  # 截断生效


def test_distinct_sample_capped_at_15(tmp_path):
    """每列取值 ≤15（VALUES_PER_COL）。"""
    import sqlite3

    p = tmp_path / "big.sqlite"
    conn = sqlite3.connect(p)
    conn.execute("CREATE TABLE t (v TEXT)")
    conn.executemany("INSERT INTO t VALUES (?)", [(f"val{i}",) for i in range(20)])
    conn.commit()
    conn.close()
    conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    try:
        ctx = build_schema_context(conn, "q", llm=None, db_path=str(p))
        section = ctx.split("取值参考：")[1]
        assert "val0" in section
        assert section.count("val") == 15
    finally:
        conn.close()


def test_fk_hint_from_id_column(tmp_path):
    """*_id 列名推断关联提示：命中同名表才提示；自引用与无对应表不提示。"""
    import sqlite3

    p = tmp_path / "shop.sqlite"
    conn = sqlite3.connect(p)
    conn.executescript(
        """
        CREATE TABLE account (account_id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE orders (order_id INTEGER PRIMARY KEY, account_id INTEGER, note TEXT);
        CREATE TABLE orphan (x_id INTEGER);
        """
    )
    conn.commit()
    conn.close()
    conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    try:
        ctx = build_schema_context(conn, "q", llm=None)
        assert "疑似引用表 account" in ctx
        assert "表 orders 列 account_id" in ctx
        hints = ctx.split("关联提示：")[1]
        assert "x_id" not in hints      # orphan.x_id 无对应表
        assert "order_id" not in hints  # orders.order_id 自引用形态不提示
    finally:
        conn.close()


def test_fk_hint_link_to_form(tmp_path):
    """link_to_X 形态列名 → 提示指向表 X（student_club 实库形态）。"""
    import sqlite3

    p = tmp_path / "club.sqlite"
    conn = sqlite3.connect(p)
    conn.executescript(
        """
        CREATE TABLE member (member_id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE income (income_id INTEGER PRIMARY KEY, link_to_member INTEGER, amount REAL);
        """
    )
    conn.commit()
    conn.close()
    conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    try:
        ctx = build_schema_context(conn, "q", llm=None)
        assert "疑似引用表 member" in ctx
    finally:
        conn.close()


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
