from qadata.graph.prompts import (
    SYSTEM_RULES,
    respond_prompt,
    sql_prompt,
    understand_prompt,
)


def test_system_rules_is_static_prefix_of_sql_prompt():
    p = sql_prompt(schema="SCHEMA_TEXT", evidence="", question="问题")
    assert p.startswith(SYSTEM_RULES)  # 缓存纪律：静态规则必须最前
    assert "SCHEMA_TEXT" in p and "问题" in p


def test_understand_prompt_contains_question():
    assert "原始问题" in understand_prompt("成绩最好的学生是谁")


def test_respond_prompt_contains_all_parts():
    p = respond_prompt(question="Q", sql="SELECT 1", rows_table="| a |", total=5, n=2)
    for part in ("Q", "SELECT 1", "| a |", "5"):
        assert part in p
