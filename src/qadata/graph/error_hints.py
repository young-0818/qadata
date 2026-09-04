"""SQL 错误分类与修复建议（规则版，M3 优化 #6）：确定性、零 token、可单测。

消息来源三类：sqlguard 中文拒绝、executor 超时、sqlite3 原生错误透传。
未命中返回 None——修复建议是锦上添花，宁缺毋滥（不添噪音）。
"""
import re

_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"语法解析失败"), "检查 SQL 语法、括号配对与引号"),
    (re.compile(r"只允许单条语句"), "输出单条 SQL，不要用分号拼接"),
    (re.compile(r"只允许 SELECT/WITH"), "本沙箱只读，只能生成查询语句"),
    (re.compile(r"引用了不存在的表"), "只用 Schema 中列出的表名"),
    (re.compile(r"已被沙箱中断"), "简化查询：减嵌套、加过滤、避免全表扫描"),
    (re.compile(r"no such column", re.IGNORECASE), "核对列名拼写，对照 Schema"),
    (re.compile(r"ambiguous column", re.IGNORECASE), "JOIN 中的列加表名前缀消除歧义"),
    (re.compile(r"no such function", re.IGNORECASE), "改用 SQLite 内置函数"),
    (re.compile(r"datatype mismatch", re.IGNORECASE), "检查比较两侧类型是否一致"),
    (re.compile(r"misuse of aggregate", re.IGNORECASE), "聚合列与非聚合列需 GROUP BY 配合"),
]


def error_hint(error_msg: str) -> str | None:
    """按序匹配第一条规则；无匹配返回 None。"""
    for pattern, hint in _RULES:
        if pattern.search(error_msg):
            return hint
    return None
