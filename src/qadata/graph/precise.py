"""多候选自一致性票决（M4-C）：执行 K 个候选 SQL，结果多重集多数派胜出。

账本纪律（spec §3.4 评审定稿）：候选批量＝一轮账本——attempts 只记一条：
多数派胜者（成功形态）／全败浓缩摘录（一条）／无多数派标记（NO_MAJORITY_ERROR）。
否决候选不进 attempts（防失败史被 N 个近似 SQL 灌满，对修复提示负贡献），
批量统计进 tracer 供归因。投票复用判分的 results_match 多重集等价（同一套语义，
不另发明相似度）。
"""
from dataclasses import dataclass

from qadata.eval.match import results_match
from qadata.tools.executor import execute_sql
from qadata.types import QueryResult

NO_MAJORITY_ERROR = "多候选结果不一致（无多数派），已取占比最高的一组转校验"

FAIL_EXCERPT_CAP = 3      # 全败摘录的相异错误条数上限
FAIL_LINE_CHARS = 60      # 摘录单条错误截断（防灌满 prompt）


@dataclass
class VoteOutcome:
    winner_sql: str | None      # None = 全部候选执行失败
    winner_result: QueryResult | None
    no_majority: bool = False
    veto_count: int = 0         # 执行失败的候选数
    fail_excerpt: str = ""      # 全败时的相异错误首行摘录


def _group_by_result(executed: list[tuple[str, QueryResult]]) -> list[dict]:
    """按执行结果多重集等价分组（判分同源，顺序无关）。"""
    groups = []
    for sql, res in executed:
        for g in groups:
            if results_match(res.rows, g["members"][0][1].rows):
                g["members"].append((sql, res))
                break
        else:
            groups.append({"members": [(sql, res)]})
    return groups


def run_precise_batch(db_path: str, candidates: list[str], *, max_rows: int,
                      timeout_s: float, tracer) -> VoteOutcome:
    """执行全部候选并按结果票决。多数派＝得票 > 候选执行数一半（唯一）；
    并列无多数派时取先出现的最大组为代表（不计正确路线，verify 强判可疑）。"""
    executed: list[tuple[str, QueryResult]] = []
    failed: list[tuple[str, str]] = []
    for sql in candidates:
        try:
            res = execute_sql(db_path, sql, max_rows=max_rows, timeout_s=timeout_s)
            executed.append((sql, res))
        except Exception as e:  # noqa: BLE001 候选级隔离：单候选失败不否决整轮
            failed.append((sql, str(e).splitlines()[0] if str(e) else "未知错误"))
    if not executed:
        excerpt = "；".join(list(dict.fromkeys(m[:FAIL_LINE_CHARS] for _, m in failed))
                            [:FAIL_EXCERPT_CAP])
        if tracer is not None:
            tracer.log("precise", candidates=len(candidates), executed=0,
                       failed=len(failed), excerpt=excerpt)
        return VoteOutcome(winner_sql=None, winner_result=None,
                           veto_count=len(failed), fail_excerpt=excerpt)
    groups = _group_by_result(executed)
    winner = max(groups, key=lambda g: len(g["members"]))  # 并列取先出现组
    rep_sql, rep_res = winner["members"][0]
    no_majority = len(winner["members"]) * 2 <= len(executed)
    if tracer is not None:
        tracer.log("precise", candidates=len(candidates), executed=len(executed),
                   failed=len(failed), groups=len(groups), majority=not no_majority)
    return VoteOutcome(winner_sql=rep_sql, winner_result=rep_res,
                       no_majority=no_majority, veto_count=len(failed))
