"""图型判定（M7 票 04）：结果集形状 → 图表形态，规则纯函数——`metrics.py` 同款纪律。

不碰 LLM、不碰图、不碰库（AST 级 import 纪律测试钉死）：图型由本模块确定性裁决，
前端只管画、不自判图型——三节组装判例的延伸（LLM 不参与排版与图型决策）。

契约＝`/api/ask` 与 SSE 末帧 answer 体的新增可选字段 `chart`（两端点同经
`answer_to_payload`，一判双达）：

- `{"type": "line",   "x": 列下标, "series": [列下标…]}`  首列时间形态＋有数值列 → 折线
- `{"type": "bar",    "x": 列下标, "series": [列下标…]}`  首列类别（非时间文本）＋有数值列 → 柱
- `{"type": "number", "x": None,   "series": [0]}`        一行×一列纯数值 → 大数卡
- `null`                                                  其余形态 → 表格（前端现状）

下标寻址 `columns`/`rows` 而非列名——SQL 结果列名可重复（`SELECT a.id, b.id`），
名字当不了键。宁缺勿滥的两条降级闸：数值列超过 `_MAX_SERIES` 不画半张图（截断
series＝图上少答了列，与数据不符，整图让位表格）；时间/数值判定要求列内**所有**
非空值同形态，混列即不是该形态（错轴比没轴更糟）。
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date
from typing import Any

# 多系列图的可读性上限：超过即判表格（前端只管画，画几张系列由这里说了算）
_MAX_SERIES = 6

# 时间形态＝确定性词法判定（库实际：BIRD financial 日期列全为文本 YYYY-MM-DD，
# M5 票 04 取证同款坑——不做语义猜测，只认书写形态）：
#   四位年 ｜ 年-月 ｜ 年-月-日（- 或 / 分隔）＋可选时间后缀（SQLite datetime 文本）
_TIME_YEAR = re.compile(r"^\d{4}$")
_TIME_DATED = re.compile(
    r"^(\d{4})[-/](\d{1,2})(?:[-/](\d{1,2}))?(?:[ T]\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?)?$"
)


def _is_number(v: Any) -> bool:
    """数值＝int/float（bool 是 int 子类，显式排除——真值列画数值图是类别误判）。"""
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _looks_time(v: Any) -> bool:
    """单个值的时间书写形态判定；月份越界（1993-13）或非法历日（1993-02-30）
    即不是时间（宁漏勿错——历日校验收进真 date 构造，不靠 1..31 粗检放行假日期）。"""
    if not isinstance(v, str):
        return False
    if _TIME_YEAR.match(v):
        return True
    m = _TIME_DATED.match(v)
    if not m:
        return False
    try:
        date(int(m.group(1)), int(m.group(2)), int(m.group(3) or 1))
    except ValueError:
        return False
    return True


def _column_kind(rows: Sequence[Sequence[Any]], idx: int) -> str:
    """列形态：'number'／'time'／'category'／'other'（全列非空值同判据，宁漏勿错）。

    行窄于列数（畸形结果）时按缺位跳过该值；整列皆空＝'other'（无从判起）。
    """
    vals = [r[idx] for r in rows if len(r) > idx and r[idx] is not None]
    if not vals:
        return "other"
    if all(_is_number(v) for v in vals):
        return "number"
    if all(isinstance(v, str) for v in vals):
        return "time" if all(_looks_time(v) for v in vals) else "category"
    return "other"


def decide_chart(columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> dict[str, Any] | None:
    """结果集形状 → 图表判定（票面四规则：时间→折线、类别＋数值→柱、单标量→大数、其余→表格）。

    返回 null 即表格——前端维持票 03 前的表格渲染，无新分支。空结果集（0 行）
    画不出任何图，如实 null。单行多列全数值不是"单标量"（N 个数没谁是主），
    判表格。
    """
    if not columns or not rows:
        return None
    if len(columns) == 1 and len(rows) == 1:
        v = rows[0][0] if rows[0] else None
        if _is_number(v):
            return {"type": "number", "x": None, "series": [0]}
        return None
    kinds = [_column_kind(rows, i) for i in range(len(columns))]
    numeric = [i for i, k in enumerate(kinds) if k == "number"]
    if not numeric or len(numeric) > _MAX_SERIES:
        return None
    if kinds[0] == "time":
        return {"type": "line", "x": 0, "series": numeric}
    if kinds[0] == "category":
        return {"type": "bar", "x": 0, "series": numeric}
    return None
