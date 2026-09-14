"""LangGraph 状态定义。total=False 允许部分初始化；各键一律整值覆盖（无 reducer）。"""
from typing import TypedDict

from qadata.types import Answer, QueryResult, SqlAttempt


class AgentState(TypedDict, total=False):
    db_path: str
    question: str
    original_question: str  # understand 改写前的原问题（多轮/审计用）
    evidence: str  # BIRD 官方业务口径说明（企业场景等价物：指标字典/口径文档）
    intent: dict | None  # M5 载体 A：understand 同调产出的六字段题面摘要（宁空勿造，未明示即 null）；
    # None＝解析失败回退态（原文当改写问题，不烧重试预算）。prompt 消费者唯一＝metric_match 填槽（票 05）；
    # 票 07 起 respond 兜底口径说明展示消费 evidence_terms（零 prompt 注入，只进答案文本组装）；
    # 尾段注入喂 generate 的软用途已判负拆除（2026-09-09 ④裁决，见 graph/intent.py 头注）
    db_schema: str
    current_sql: str | None
    result: QueryResult | None
    attempts: list[SqlAttempt]
    last_error: str | None
    verify_note: str | None  # verify 判定可疑的原因；None=通过或未经 verify
    precise_candidates: list[str] | None  # M4-C 载荷键（非预算键）：同轮多候选的 SQL 列表；
    # 账本仍由 len(attempts) 承担——批量=一轮，execute 用完即清（整值覆盖语义显式置 None）
    matched_metric: str | None  # M5 票 05：命中指标名（载荷键）——兼作 respond 血缘展示与评测记录来源；
    # 兜底路径清 None，降级时 explore 显式覆盖（防路由二次降级，仿 precise_candidates 纪律）
    metric_note: str | None  # M5 票 05：指标模板降级原因（执行失败/校验可疑）；None=未发生降级。
    # 进失败历史（generate 可见）与 respond 标注
    session_context: dict | None  # M7 票 05 唯一新状态键（precise_candidates 同款载荷纪律：显式键、
    # 整值覆盖）：{"turns": [L2 行 {question/sql/row_count/head/failed，≤K=5 条·时间升序}]，
    # "draft": L1 {sql/head} 或 None}。仅 understand/generate 消费（姊妹钉测＝
    # test_generate_never_reads_intent 同款条款，tests/test_session_context.py AST 源扫描
    # 钉死其他节点不得读取）；None/缺键＝无会话关态，understand/generate prompt 与现状逐字节一致。
    # L3 全史归档永不进 prompt（K=5 窗口与草稿资格在 web 层 build_session_context 切好再装填）。
    answer: Answer | None
