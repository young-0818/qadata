import pytest

from qadata.tools.executor import execute_sql
from qadata.types import SqlExecutionError


def test_select_returns_result(fixture_db):
    r = execute_sql(fixture_db, "SELECT name FROM students WHERE id = 1")
    assert r.columns == ["name"]
    assert r.rows == [("Alice",)]
    assert r.truncated is False


def test_row_cap_marks_truncation(fixture_db):
    r = execute_sql(fixture_db, "SELECT student_id, subject, score FROM scores", max_rows=2)
    assert len(r.rows) == 2 and r.truncated is True


def test_dml_rejected_by_whitelist(fixture_db):
    # INSERT 会先被语句白名单拦截（mode=ro 只读连接是纵深防御的第二层）
    with pytest.raises(SqlExecutionError, match="只允许"):
        execute_sql(fixture_db, "INSERT INTO students VALUES (9, 'Eve', 1)")


def test_syntax_error_raises_readable(fixture_db):
    with pytest.raises(SqlExecutionError, match="no such column"):
        execute_sql(fixture_db, "SELECT nope FROM students")


def test_non_select_rejected(fixture_db):
    with pytest.raises(SqlExecutionError, match="只允许"):
        execute_sql(fixture_db, "DROP TABLE students")


def test_missing_db_raises_readable(tmp_path):
    # 幻觉/错误库路径是最常见的 LLM 失败：必须收敛为可读 SqlExecutionError，绝不漏裸 sqlite3 异常
    with pytest.raises(SqlExecutionError, match="无法打开数据库"):
        execute_sql(str(tmp_path / "no_such_dir" / "missing.sqlite"), "SELECT 1")
