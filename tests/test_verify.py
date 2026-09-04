from qadata.graph.verify import Verdict, verify_result
from qadata.types import QueryResult


def _result(rows, columns=("v",), truncated=False):
    return QueryResult(columns=list(columns), rows=rows, row_count=len(rows),
                       truncated=truncated, elapsed_ms=1)


def test_empty_result_suspicious():
    v = verify_result("有多少人", "SELECT COUNT(*) FROM students", _result([]))
    assert isinstance(v, Verdict)
    assert v.passed is False and "空" in v.reason


def test_aggregate_negative_suspicious():
    v = verify_result("有多少人", "SELECT COUNT(*) FROM students", _result([(-1,)]))
    assert v.passed is False and "异常" in v.reason


def test_aggregate_null_suspicious():
    v = verify_result("总额", "SELECT SUM(score) FROM scores", _result([(None,)]))
    assert v.passed is False


def test_normal_aggregate_passes():
    v = verify_result("有多少人", "SELECT COUNT(*) FROM students", _result([(2,)]))
    assert v.passed is True and v.reason is None


def test_truncated_not_suspicious():
    rows = [(i,) for i in range(10)]
    v = verify_result("列出全部", "SELECT * FROM students", _result(rows, truncated=True))
    assert v.passed is True and v.reason is None


def test_empty_list_all_passes():
    """M3 靶子：列出全部类问题空结果合法（M2 改坏主因的治本）。"""
    v = verify_result("列出所有学生", "SELECT name FROM students", _result([]))
    assert v.passed is True and v.reason is None


def test_empty_aggregate_question_still_suspicious():
    v = verify_result("有多少人", "SELECT COUNT(*) FROM students", _result([]))
    assert v.passed is False and "空" in v.reason


def test_normal_rows_pass():
    v = verify_result("谁成绩好", "SELECT name FROM students", _result([("Alice",)]))
    assert v.passed is True


def test_non_aggregate_negative_value_passes():
    """R2 只盯聚合查询：普通查询的负数值是合法数据。"""
    v = verify_result("温度", "SELECT temp FROM t", _result([(-5,)], columns=("temp",)))
    assert v.passed is True
