"""M7 票 04 单测：图型判定纯函数（web/charts.py）——四类判定＋边界＋AST import 纪律。

票面纪律与 metrics.py 同款：不碰 LLM 不碰图不开库，AST 级钉死；判定矩阵在纯码层
打满（折线/柱/大数卡/表格＋混列/空结果/全标量边界），端点面 chart 字段并入形状
由 tests/test_web_api.py 契约测试钉（同一 answer_to_payload 本体，两端点不漂移）。
"""
import ast
import inspect

from qadata.web import charts as charts_mod
from qadata.web.charts import decide_chart

# ── 折线：首列时间书写形态＋存在数值列 ──────────────────────────────


def test_line_month_series():
    cols = ["month", "amount"]
    rows = [["1993-01", 10], ["1993-02", 20], ["1993-03", 15]]
    assert decide_chart(cols, rows) == {"type": "line", "x": 0, "series": [1]}


def test_line_accepts_year_and_datetime_forms():
    for rows in ([["1993", 1.5], ["1994", 2.5]],          # 裸年
                 [["1993/2", 1], ["1993/3", 2]],           # 斜杠年月
                 [["1993-02-01 00:00:00", 1], ["1993-03-01 00:00:00", 2]],  # datetime 文本
                 [["1993-02-01", 1], ["1993-03-05", 2]]):  # 全日期（financial 库实际形态）
        assert decide_chart(["d", "v"], rows)["type"] == "line"


def test_line_only_numeric_series_drops_text_columns():
    """混列：时间＋文本＋两个数值→折线只画数值列（文本列不是可画系列）。"""
    rows = [["1993-01", "A", 1, 2], ["1993-02", "B", 3, 4]]
    assert decide_chart(["month", "cat", "v1", "v2"], rows) == {
        "type": "line", "x": 0, "series": [2, 3]}


def test_time_column_with_no_numeric_is_table():
    assert decide_chart(["month"], [["1993-01"], ["1993-02"]]) is None


def test_mixed_time_text_column_is_category_not_time():
    """列内混形态（有月份也有 'N/A'）＝不是时间列；配数值列走柱。"""
    rows = [["1993-01", 1], ["N/A", 2]]
    assert decide_chart(["m", "v"], rows) == {"type": "bar", "x": 0, "series": [1]}


def test_invalid_month_is_not_time():
    rows = [["1993-13", 1], ["1993-99", 2]]
    assert decide_chart(["m", "v"], rows)["type"] == "bar"


def test_invalid_calendar_day_is_not_time():
    """历日校验走真 date 构造：1993-02-30/06-31 是书写像时间的假日期→类别列。"""
    rows = [["1993-02-30", 1], ["1993-06-31", 2]]
    assert decide_chart(["d", "v"], rows)["type"] == "bar"


def test_leap_day_is_time():
    rows = [["2024-02-29", 1], ["2024-03-01", 2]]
    assert decide_chart(["d", "v"], rows)["type"] == "line"


# ── 柱：首列类别（非时间文本）＋数值列 ──────────────────────────────


def test_bar_category_numeric():
    rows = [["math", 95.5], ["english", 90.0]]
    assert decide_chart(["subject", "score"], rows) == {
        "type": "bar", "x": 0, "series": [1]}


def test_bar_first_col_numeric_is_table():
    """首列是数值（如 id 开头）＝没有可当类别轴的列，判表格不错画。"""
    assert decide_chart(["id", "grade"], [[1, 3], [2, 2]]) is None


def test_bar_multi_numeric_columns():
    rows = [["A", 1, 2], ["B", 3, 4]]
    assert decide_chart(["c", "v1", "v2"], rows) == {
        "type": "bar", "x": 0, "series": [1, 2]}


def test_too_many_series_falls_to_table():
    """数值列超上限：截断 series＝图上少答了列，与数据不符→整图让位表格。"""
    cols = ["cat"] + [f"v{i}" for i in range(7)]
    rows = [["A"] + [i for i in range(7)], ["B"] + [i * 2 for i in range(7)]]
    assert decide_chart(cols, rows) is None
    ok_cols = ["cat"] + [f"v{i}" for i in range(6)]
    ok_rows = [["A"] + [i for i in range(6)], ["B"] + [i * 2 for i in range(6)]]
    assert decide_chart(ok_cols, ok_rows)["type"] == "bar"


# ── 大数卡：一行×一列纯数值 ─────────────────────────────────────────


def test_number_single_scalar():
    assert decide_chart(["cnt"], [[2]]) == {"type": "number", "x": None, "series": [0]}
    assert decide_chart(["avg"], [[151410.18]])["type"] == "number"


def test_single_text_cell_is_table():
    """1×1 文本不是"大数"——单值字符串（如查出的名字）判表格。"""
    assert decide_chart(["name"], [["Bob"]]) is None


def test_single_row_multi_numeric_is_table():
    """全标量边界：一行多列数值——N 个数没谁是主，不配大数卡，判表格。"""
    assert decide_chart(["a", "b"], [[1, 2.5]]) is None


def test_single_col_multi_rows_numeric_is_table():
    assert decide_chart(["amount"], [[1], [2], [3]]) is None


def test_bool_cell_is_not_number():
    assert decide_chart(["flag"], [[True]]) is None


# ── 表格兜底：空结果/畸形输入如实 null ──────────────────────────────


def test_empty_result_is_table():
    assert decide_chart(["a", "b"], []) is None
    assert decide_chart([], []) is None


def test_null_heavy_column_still_number_if_all_nonnull_numeric():
    rows = [["A", 1], ["B", None], ["C", 3]]
    assert decide_chart(["c", "v"], rows) == {"type": "bar", "x": 0, "series": [1]}


def test_all_null_column_is_other():
    rows = [["A", None], ["B", None]]
    assert decide_chart(["c", "v"], rows) is None


def test_bool_column_is_not_numeric():
    """列形态分支：bool 列（全 True/False）不算数值（与 1×1 标量分支各钉一处）。"""
    rows = [["A", True], ["B", False]]
    assert decide_chart(["c", "flag"], rows) is None


# ── 零污染哨兵：AST 级 import 纪律（metrics 先例同款，票面勾选①）────


def test_charts_module_touches_no_llm_graph_or_db():
    """图型判定纯函数纪律：AST 钉死 import 面——只允许标准库（re/typing/
    collections.abc），qadata.*（含 graph/llm/tools）与非标库一律禁。

    用 ast 解析 import 语句而非读源码正则（防注释/docstring 提及式假阳性）。
    """
    tree = ast.parse(inspect.getsource(charts_mod))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module)
    allowed = {"__future__", "re", "typing", "collections", "datetime"}  # 比的是顶层包名
    offenders = {m for m in imported if m.split(".")[0] not in allowed}
    assert not offenders, f"charts 模块违规导入：{offenders}"
    assert not any(m.startswith("qadata.") for m in imported)
