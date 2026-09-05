"""题型标签器：规则版六类分类（M4-E2 题型切片与 P0 归因共用同一套标签）。

六类：趋势/对比/排名/极值/分布/明细。识别不了回「明细」（兜底，不判错）。
优先级即触发链顺序——先对比再趋势再排名再极值再分布（「highest above average」
类歧义题按 gold 形态属极值，故极值压在分布之前；排名压在极值之前是同一理由的
「top N」特化）。
"""
import re

# (标签, 触发词表)：触发词以 \\b 开头的按词边界正则匹配（忽略大小写），
# 其余按短语子串匹配（含空格避免误伤 first_name / almost 等）。
_TRIGGER_CHAIN = (
    ("对比", ("\\bcompar", "\\bratio\\b", "\\bpercentage\\b", "\\bdeviation\\b",
              "\\bdifference\\b", "\\bversus\\b", " vs ")),
    ("趋势", ("\\btrend", "over time", "over the years", "by year", "per year",
              "each year", "\\bannual", "\\bmonthly", "\\byearly", "\\bquarterly",
              "\\bincrease", "\\bdecrease")),
    ("排名", ("\\brank", "top ", "first ", "second ", "third ", "fourth ", "fifth ")),
    ("极值", ("\\bhighest", "\\blowest", "\\bmaximum", "\\bminimum", "\\bmax\\b",
              "\\bmin\\b", "\\blongest", "\\bshortest", "\\blargest", "\\bsmallest",
              "\\bgreatest", "\\bfewest", "\\boldest", "\\bnewest", "\\byoungest",
              "\\btallest", "\\bbiggest", "\\bmost\\b")),
    ("分布", ("\\baverage", "how many", "number of", "count of", "count the",
              "\\bdistribution", "\\bproportion", "\\bbreakdown")),
)


def _has(text: str, trigger: str) -> bool:
    if trigger.startswith("\\b"):
        return re.search(trigger, text, re.IGNORECASE) is not None
    return trigger.lower() in text.lower()


def _label_from(text: str) -> str:
    for label, triggers in _TRIGGER_CHAIN:
        if any(_has(text, t) for t in triggers):
            return label
    return "明细"


def label_question_type(question: str, evidence: str = "") -> str:
    """按题面判题型；题面无信号（明细兜底）才叠加 evidence 复扫——
    防止口径注释里的词（如 first_name、cell count）污染题面语义。"""
    label = _label_from(question or "")
    if label == "明细" and evidence:
        return _label_from(f"{question} {evidence}")
    return label