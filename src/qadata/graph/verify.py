"""verify 规则版（M2）：确定性校验不烧 token；LLM 辅助校验归 M3 优化 #6。"""
import re
from dataclasses import dataclass

from qadata.types import QueryResult

_AGG_RE = re.compile(r"\b(COUNT|SUM|AVG|MIN|MAX)\s*\(", re.IGNORECASE)


@dataclass
class Verdict:
    passed: bool
    reason: str | None = None


def verify_result(question: str, sql: str, result: QueryResult) -> Verdict:
    """三条规则按序检查，命中即返回可疑（原因进失败历史，给重试方向）。"""
    if result.row_count == 0:
        return Verdict(False, "结果为空")
    if result.row_count == 1 and len(result.columns) == 1 and _AGG_RE.search(sql):
        v = result.rows[0][0]
        if v is None or (isinstance(v, (int, float)) and v < 0):
            return Verdict(False, "聚合结果异常（负数或 NULL）")
    if result.truncated:
        return Verdict(False, "结果被截断，可能未取全")
    return Verdict(True)
