import pytest

from qadata.graph.error_hints import error_hint


@pytest.mark.parametrize("msg,expected", [
    ("SQL 安全检查未通过：语法解析失败——Invalid expression", "语法"),
    ("SQL 安全检查未通过：只允许单条语句，收到 2 条", "单条"),
    ("SQL 安全检查未通过：只允许 SELECT/WITH 查询，收到 Insert", "只读"),
    ("SQL 安全检查未通过：引用了不存在的表 Foo", "Schema"),
    ("查询超时（>5.0 秒），已被沙箱中断", "简化"),
    ("no such column: nope", "拼写"),
    ("ambiguous column name: name", "前缀"),
    ("no such function: FOO", "内置函数"),
    ("datatype mismatch", "类型"),
    ("misuse of aggregate: SUM()", "GROUP BY"),
])
def test_error_hint_matches(msg, expected):
    assert expected in error_hint(msg)


def test_error_hint_unknown_returns_none():
    assert error_hint("some totally unrelated error") is None
