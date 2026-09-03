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


def test_timeout_interrupts_long_query(fixture_db):
    """递归 CTE 大数据量，小超时必被 progress handler 中断。"""
    sql = ("WITH RECURSIVE cnt(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM cnt "
           "WHERE x < 5000000) SELECT count(*) FROM cnt")
    with pytest.raises(SqlExecutionError, match="超时"):
        execute_sql(fixture_db, sql, timeout_s=0.05)


@pytest.fixture
def big_db(tmp_path):
    """25 行数字表：测双行数上限。"""
    import sqlite3
    p = tmp_path / "big.sqlite"
    conn = sqlite3.connect(p)
    conn.execute("CREATE TABLE nums (x INTEGER)")
    conn.executemany("INSERT INTO nums VALUES (?)", [(i,) for i in range(25)])
    conn.commit()
    conn.close()
    return str(p)


def test_fetch_cap_hit_reports_true_total(big_db):
    """撞获取上限：row_count 用 COUNT(*) 报真值，rows 只给显示上限。"""
    r = execute_sql(big_db, "SELECT x FROM nums", max_rows=5, fetch_cap=10)
    assert r.row_count == 25
    assert r.truncated is True
    assert len(r.rows) == 5


def test_under_fetch_cap_exact(big_db):
    r = execute_sql(big_db, "SELECT x FROM nums", max_rows=5, fetch_cap=30)
    assert r.row_count == 25 and len(r.rows) == 5 and r.truncated is True
    r2 = execute_sql(big_db, "SELECT x FROM nums", max_rows=30, fetch_cap=30)
    assert r2.row_count == 25 and len(r2.rows) == 25 and r2.truncated is False


def test_sqlguard_reject_before_execution(big_db):
    """语句层前置：INSERT 在 sqlguard 即被拦（消息含"只允许"，旧测试兼容）。"""
    with pytest.raises(SqlExecutionError, match="只允许"):
        execute_sql(big_db, "INSERT INTO nums VALUES (99)")


def test_hallucinated_table_rejected_before_execution(big_db):
    with pytest.raises(SqlExecutionError, match="不存在的表"):
        execute_sql(big_db, "SELECT * FROM no_such_table")


def test_fetch_cap_count_timeout_degrades(fixture_db):
    """终审建议回归：撞获取上限后的 COUNT(*) 真值探索若超时，降级为近似总数——
    取数已成功的查询不得被"锦上添花"的精确总数打成失败（沙箱不误伤合法路径）。"""
    sql = ("WITH RECURSIVE cnt(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM cnt "
           "WHERE x < 5000000) SELECT x FROM cnt")
    r = execute_sql(fixture_db, sql, max_rows=2, fetch_cap=2, timeout_s=0.3)
    assert r.row_count == 3  # 降级：len(fetched) = fetch_cap + 1 的近似下界
    assert r.truncated is True and len(r.rows) == 2
