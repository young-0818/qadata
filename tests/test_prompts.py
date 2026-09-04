from qadata.graph.prompts import (
    SYSTEM_RULES,
    format_failure_history,
    respond_prompt,
    sql_prompt,
    strip_conclusion_prefix,
    understand_prompt,
)
from qadata.types import SqlAttempt


def test_system_rules_is_static_prefix_of_sql_prompt():
    p = sql_prompt(schema="SCHEMA_TEXT", evidence="", question="问题")
    assert p.startswith(SYSTEM_RULES)  # 缓存纪律：静态规则必须最前
    assert "SCHEMA_TEXT" in p and "问题" in p


def test_system_rules_forbids_formatting():
    assert "不格式化输出" in SYSTEM_RULES
    p = sql_prompt(schema="S", evidence="", question="Q")
    assert "不格式化输出" in p and p.startswith(SYSTEM_RULES)


def test_system_rules_forbids_extra_columns():
    """M3 验证跑归因：5 道改坏题 4 道是「多选列」形态（多集匹配下多列即错）。"""
    assert "只选问题需要的列" in SYSTEM_RULES
    p = sql_prompt(schema="S", evidence="", question="Q")
    assert "只选问题需要的列" in p and p.startswith(SYSTEM_RULES)


def test_understand_prompt_contains_question():
    assert "原始问题" in understand_prompt("成绩最好的学生是谁")


def test_respond_prompt_contains_all_parts():
    p = respond_prompt(question="Q", sql="SELECT 1", rows_table="| a |", total=5, n=2)
    for part in ("Q", "SELECT 1", "| a |", "5"):
        assert part in p


def test_sql_prompt_static_prefix_preserved_with_history():
    p = sql_prompt("S", "", "Q", history="## 之前的失败尝试\n尝试 1：...")
    assert p.startswith(SYSTEM_RULES)
    assert "之前的失败尝试" in p


def test_sql_prompt_empty_history_no_section():
    p = sql_prompt("S", "", "Q")
    assert "之前的失败尝试" not in p


def test_failure_history_empty():
    assert format_failure_history([], None) == ""


def test_failure_history_renders_error_first_line():
    a = SqlAttempt(sql="SELECT nope FROM students", error="no such column: nope\n更多行")
    h = format_failure_history([a], None)
    assert "尝试 1：SELECT nope FROM students" in h
    assert "no such column: nope" in h and "更多行" not in h


def test_failure_history_extract_failure():
    h = format_failure_history([SqlAttempt(sql="", error="回复中未找到合法 SQL")], None)
    assert "未能提取出合法 SQL" in h


def test_failure_history_verify_note_on_last_success():
    attempts = [
        SqlAttempt(sql="SELECT bad", error="no such column: bad"),
        SqlAttempt(sql="SELECT name FROM students", row_count=0),
    ]
    h = format_failure_history(attempts, "结果为空")
    assert "上次执行成功但校验未通过——结果为空" in h


def test_failure_history_appends_repair_hint():
    a = SqlAttempt(sql="SELECT nope FROM students", error="no such column: nope")
    h = format_failure_history([a], None)
    assert "修复建议：核对列名拼写" in h


def test_strip_conclusion_prefix():
    assert strip_conclusion_prefix("结论：Alice 最好") == "Alice 最好"
    assert strip_conclusion_prefix("结论:Alice") == "Alice"
    assert strip_conclusion_prefix("Alice 最好") == "Alice 最好"


def test_strip_conclusion_prefix_multiple_layers():
    assert strip_conclusion_prefix("结论：结论：Alice 最好") == "Alice 最好"
    assert strip_conclusion_prefix("结论:结论：结论：Bob") == "Bob"
