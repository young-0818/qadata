"""核心数据类型：Agent 状态与结果载体。"""
from dataclasses import dataclass


class SqlExecutionError(Exception):
    """SQL 执行失败（语法错/列不存在/只读拒绝等），message 为可读错误。"""


@dataclass
class SqlAttempt:
    """一次 SQL 生成+执行的记录（失败历史素材，M2 自纠错将直接复用）。"""

    sql: str
    error: str | None = None
    row_count: int | None = None


@dataclass
class QueryResult:
    """一次查询的执行结果（rows 可能被截断）。"""

    columns: list[str]
    rows: list[tuple]  # 截断后的行
    row_count: int  # 实际取到的行数
    truncated: bool  # True 表示真实结果比 rows 多
    elapsed_ms: int


@dataclass
class Answer:
    """最终答案。failed=True 时 conclusion 为诚实的失败说明。"""

    conclusion: str
    sql: str | None = None
    result: QueryResult | None = None
    failed: bool = False
    error_summary: str | None = None
