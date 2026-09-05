"""LangGraph 状态定义。total=False 允许部分初始化；各键一律整值覆盖（无 reducer）。"""
from typing import TypedDict

from qadata.types import Answer, QueryResult, SqlAttempt


class AgentState(TypedDict, total=False):
    db_path: str
    question: str
    original_question: str  # understand 改写前的原问题（多轮/审计用）
    evidence: str  # BIRD 官方业务口径说明（企业场景等价物：指标字典/口径文档）
    db_schema: str
    current_sql: str | None
    result: QueryResult | None
    attempts: list[SqlAttempt]
    last_error: str | None
    verify_note: str | None  # verify 判定可疑的原因；None=通过或未经 verify
    precise_candidates: list[str] | None  # M4-C 载荷键（非预算键）：同轮多候选的 SQL 列表；
    # 账本仍由 len(attempts) 承担——批量=一轮，execute 用完即清（整值覆盖语义显式置 None）
    answer: Answer | None
