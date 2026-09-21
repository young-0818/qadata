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
    """最终答案。respond 产出的 conclusion 为票 07 三节组装文本（【结论】→【数据依据】→
    【口径说明】→【校验标注】，空节省略；判分在结果集层，不读此文本）。failed=True 时为
    诚实失败说明；最外层守护（run_question 兜裸异常）不经 respond，保持单段纯文本。
    M8 票 03 澄清态：failed=False、无 SQL、conclusion＝澄清问本身（非三节组装）。"""

    conclusion: str
    sql: str | None = None
    result: QueryResult | None = None
    failed: bool = False
    error_summary: str | None = None
    # M8 票 03（默认关）：口径缺失到「任何 SQL 都是猜」时 understand 回问的一句澄清；
    # None＝非澄清轮（M5 评测路径字段 path/metric_name/template_fell_back 已随指标层
    # 退役删除，ADR-0007——本键升尾）
    clarification: str | None = None
