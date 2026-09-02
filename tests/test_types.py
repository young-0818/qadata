import pytest

from qadata.types import Answer, QueryResult, SqlAttempt, SqlExecutionError


def test_sql_attempt_defaults():
    a = SqlAttempt(sql="SELECT 1")
    assert a.error is None and a.row_count is None


def test_query_result_holds_truncation_info():
    r = QueryResult(columns=["id"], rows=[(1,)], row_count=1, truncated=False, elapsed_ms=3)
    assert r.truncated is False and r.elapsed_ms == 3


def test_answer_failed_path():
    a = Answer(conclusion="失败", failed=True, error_summary="no such column")
    assert a.sql is None and a.failed and a.result is None


def test_sql_execution_error_is_exception():
    with pytest.raises(SqlExecutionError, match="boom"):
        raise SqlExecutionError("boom")
