from qadata.graph.prompts import (
    SYSTEM_RULES,
    compose_conclusion,
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


def test_system_rules_forbids_stringification_with_questiontext_exemption():
    """M4-D（228 探针实证）：禁字符串化/拼接 %，但题面明示百分比或精度时按题面计算保留。

    现行「不格式化输出」压制了模型按题面做 ×100/ROUND 的意愿（A 规则 0/3 → B 规则 3/3 判对）。
    """
    assert "不字符串化输出" in SYSTEM_RULES
    assert "百分比或小数精度" in SYSTEM_RULES
    assert "不要拼接 %" in SYSTEM_RULES
    p = sql_prompt(schema="S", evidence="", question="Q")
    assert "不字符串化输出" in p and p.startswith(SYSTEM_RULES)  # 前缀缓存纪律不变


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


def test_respond_prompt_asks_for_freeform_report():
    """M8 票 07 报告化：索要「报告式总结＋量化发现」，不再要一句话（旧钉翻正）；
    不套固定骨架＝指令里不得出现结构模板/字数骨架。"""
    p = respond_prompt(question="Q", sql="SELECT 1", rows_table="| a |", total=5, n=2)
    assert "报告式总结" in p and "量化" in p
    assert "一句话结论" not in p
    assert "简述数据依据" not in p  # 勿再索要——模型代劳数据依据会掺编造
    assert "禁止编造" in p          # 数字仍只准来自结果集（永不编造不破）
    assert "【】" in p              # 系统节头勿模仿（前端白名单是机制闸，此为纵深）


def test_respond_prompt_keeps_token_cost_out_of_report():
    """票 06 边界承袭（spec §3）：token/费用数字不进答案内容——指令显式排除。"""
    p = respond_prompt(question="Q", sql="SELECT 1", rows_table="| a |", total=5, n=2)
    assert "token" in p and "费用" in p  # 以「不要提」的形式在场
    assert "不要提 token/耗时/费用" in p


# ── M5 票 07：E1 三节组装（纯函数）────────────────────────────────


def test_compose_conclusion_three_sections_in_order():
    t = compose_conclusion("Alice 最好",
                           basis="共取到 2 行（全部列示）；所用表：students、scores",
                           caliber="命中指标「贷款笔数」（loan_count）；口径：按批准日期计条",
                           notes=["该结果未通过自动校验：结果为空"])
    assert t.startswith("【结论】Alice 最好")
    for head in ("【数据依据】", "【口径说明】", "【校验标注】"):
        assert head in t
    assert (t.index("【结论】") < t.index("【数据依据】")
            < t.index("【口径说明】") < t.index("【校验标注】"))
    assert "- 该结果未通过自动校验：结果为空" in t  # 校验标注逐条列、原样接入


def test_compose_conclusion_omits_empty_sections_no_idle():
    """空节省略不空转：校验标注全 None 时连节头都不出（票面条款）。"""
    t = compose_conclusion("q", basis="共取到 1 行")
    assert "【数据依据】" in t
    assert "【口径说明】" not in t and "【校验标注】" not in t


def test_compose_conclusion_bare_conclusion():
    assert compose_conclusion("就一句话") == "【结论】就一句话"


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


# ── M5 裁决⑧：understand 裸时间微调（票 05 复核 prompt 族测已随指标层退役，ADR-0007）


def test_understand_prompt_demands_bare_time_filters():
    """裁决⑧（04→05 移交）：时间类 filters 输出裸时间表达式——否则填槽对英文题面系统性失效。"""
    p = understand_prompt("原始问题", "ev")
    assert "裸时间" in p and "in 1993" in p
