import pytest

from qadata.tools.sqlguard import used_tables, validate_sql
from qadata.types import SqlExecutionError

TABLES = ["students", "scores"]


@pytest.mark.parametrize("sql", [
    "SELECT 1",
    "SELECT name FROM students WHERE id = 1",
    "WITH t AS (SELECT 1) SELECT * FROM t",
    "SELECT 1 UNION SELECT 2",
    "SELECT a.name FROM students a INTERSECT SELECT name FROM scores",
    "SELECT * FROM Students",  # 大小写不敏感
])
def test_allowed_statements_pass(sql):
    assert validate_sql(sql, TABLES).strip() == sql.strip()


@pytest.mark.parametrize("sql", [
    "INSERT INTO students VALUES (9, 'Eve', 1)",
    "UPDATE students SET name = 'x'",
    "DELETE FROM students",
    "DROP TABLE students",
    "CREATE TABLE evil (a int)",
    "PRAGMA table_info(students)",
    'ATTACH "other.sqlite" AS other',
    "VACUUM",
])
def test_mutating_statements_rejected(sql):
    with pytest.raises(SqlExecutionError, match="安全检查"):
        validate_sql(sql, TABLES)


def test_cte_wrapped_insert_rejected():
    """M1 首单词法的洞：WITH 开头的变异语句。"""
    with pytest.raises(SqlExecutionError, match="安全检查"):
        validate_sql("WITH t AS (SELECT 1) INSERT INTO students SELECT * FROM t", TABLES)


def test_multi_statement_rejected():
    with pytest.raises(SqlExecutionError, match="单条语句"):
        validate_sql("SELECT 1; DROP TABLE students", TABLES)


def test_unknown_table_rejected():
    with pytest.raises(SqlExecutionError, match="hallucinated"):
        validate_sql("SELECT * FROM hallucinated", TABLES)


def test_cte_alias_not_flagged_as_unknown():
    """CTE 别名出现在 FROM 中，不得误判为未知表。"""
    assert "WITH t AS" in validate_sql("WITH t AS (SELECT 1) SELECT * FROM t", TABLES)


def test_empty_rejected():
    with pytest.raises(SqlExecutionError, match="空语句"):
        validate_sql("   ", TABLES)


def test_garbage_syntax_rejected():
    with pytest.raises(SqlExecutionError, match="语法解析失败"):
        validate_sql("SELECT FROM WHERE", TABLES)


def test_unknown_table_in_join_rejected():
    """终审补强：JOIN 体内的未知表同样拒绝（find_all 全树扫描语义钉死）。"""
    with pytest.raises(SqlExecutionError, match="hallucinated"):
        validate_sql("SELECT s.name FROM students s JOIN hallucinated h ON s.id = h.id", TABLES)


def test_unknown_table_in_subquery_rejected():
    with pytest.raises(SqlExecutionError, match="hallucinated"):
        validate_sql("SELECT * FROM (SELECT * FROM hallucinated)", TABLES)


# ── used_tables（票 07：respond 数据依据节的确定性表名提取）──────────


def test_used_tables_join_sorted_unique():
    sql = ("SELECT s.name FROM students s JOIN scores sc ON s.id = sc.student_id "
           "JOIN scores x ON x.student_id = s.id")
    assert used_tables(sql) == ["scores", "students"]


def test_used_tables_excludes_cte_alias():
    assert used_tables("WITH t AS (SELECT 1) SELECT * FROM t") == []


def test_used_tables_cte_over_real_table():
    sql = "WITH t AS (SELECT id FROM students) SELECT * FROM t JOIN scores ON 1=1"
    assert used_tables(sql) == ["scores", "students"]


def test_used_tables_subquery_and_union():
    assert used_tables("SELECT * FROM (SELECT a FROM x) UNION SELECT b FROM y") == ["x", "y"]


def test_used_tables_unparseable_returns_empty_not_raise():
    """显示辅助而非沙箱闸：解析不了返回空表，绝不抛（拒绝语义归 validate_sql）。"""
    assert used_tables("SELECT FROM WHERE") == []
    assert used_tables("") == []
    assert used_tables("SELECT 1; SELECT 2") == []  # 非单语句不猜
