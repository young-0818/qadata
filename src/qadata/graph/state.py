"""LangGraph 状态定义。total=False 允许部分初始化；各键一律整值覆盖（无 reducer）。"""
from typing import TypedDict

from qadata.types import Answer, QueryResult, SqlAttempt


class AgentState(TypedDict, total=False):
    db_path: str
    question: str
    evidence: str  # BIRD 官方业务口径说明（企业场景等价物：指标字典/口径文档）
    db_schema: str
    current_sql: str | None
    result: QueryResult | None
    attempts: list[SqlAttempt]
    last_error: str | None
    answer: Answer | None
