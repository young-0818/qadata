"""verify 规则版（M2/M3）：确定性校验不烧 token；LLM 辅助校验归后置优化。"""
import re
from dataclasses import dataclass

from qadata.types import QueryResult

_AGG_RE = re.compile(r"\b(COUNT|SUM|AVG|MIN|MAX)\s*\(", re.IGNORECASE)
# 列出全部类问题：空结果合法（M2 改坏主因——「列出全部」空结果被误判触发重试）
_LIST_ALL_RE = re.compile(r"列出\s*(全部|所有|所有的)|有哪些|哪些\S*(都|全)")


@dataclass
class Verdict:
    passed: bool
    reason: str | None = None


def verify_result(question: str, sql: str, result: QueryResult) -> Verdict:
    """规则按序检查；命中即返回可疑（原因进失败历史，给重试方向）。"""
    if result.row_count == 0 and not _LIST_ALL_RE.search(question):
        return Verdict(False, "结果为空")
    if result.row_count == 1 and len(result.columns) == 1 and _AGG_RE.search(sql):
        v = result.rows[0][0]
        if v is None or (isinstance(v, (int, float)) and v < 0):
            return Verdict(False, "聚合结果异常（负数或 NULL）")
    # 截断不再判可疑（M3）：「结果太大」重试修不好，由 respond 标注截断
    return Verdict(True)
