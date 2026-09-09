"""LangGraph 状态定义。total=False 允许部分初始化；各键一律整值覆盖（无 reducer）。"""
from typing import TypedDict

from qadata.types import Answer, QueryResult, SqlAttempt


class AgentState(TypedDict, total=False):
    db_path: str
    question: str
    original_question: str  # understand 改写前的原问题（多轮/审计用）
    evidence: str  # BIRD 官方业务口径说明（企业场景等价物：指标字典/口径文档）
    intent: dict | None  # M5 载体 A：understand 同调产出的六字段题面摘要（宁空勿造，未明示即 null）；
    # None＝解析失败回退态（原文当改写问题，不烧重试预算）。唯一消费者＝metric_match 填槽（票 05）；
    # 尾段注入喂 generate 的软用途已判负拆除（2026-09-09 ④裁决，见 graph/intent.py 头注）
    db_schema: str
    current_sql: str | None
    result: QueryResult | None
    attempts: list[SqlAttempt]
    last_error: str | None
    verify_note: str | None  # verify 判定可疑的原因；None=通过或未经 verify
    precise_candidates: list[str] | None  # M4-C 载荷键（非预算键）：同轮多候选的 SQL 列表；
    # 账本仍由 len(attempts) 承担——批量=一轮，execute 用完即清（整值覆盖语义显式置 None）
    answer: Answer | None
