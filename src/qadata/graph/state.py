"""LangGraph 状态定义。total=False 允许部分初始化；各键一律整值覆盖（无 reducer）。"""
from typing import TypedDict

from qadata.types import Answer, QueryResult, SqlAttempt


class AgentState(TypedDict, total=False):
    db_path: str
    question: str
    original_question: str  # understand 改写前的原问题（多轮/审计用）
    # evidence 键已删（ADR-0008，2026-09-21）：口径唯一通道＝字典检索块经 Select 格
    # 进 generate，不再直塞状态。
    intent: dict | None  # M5 载体 A：understand 同调产出的六字段题面摘要（宁空勿造，未明示即 null）；
    # None＝解析失败回退态（原文当改写问题，不烧重试预算）。消费者＝值链搭车抽词（M10 票 03）
    # ＋ respond 兜底口径说明展示消费 evidence_terms（零 prompt 注入，只进答案文本组装）；
    # 尾段注入喂 generate 的软用途已判负拆除（2026-09-09 ④裁决，见 graph/intent.py 头注）；
    # metric_match 填槽消费者已随指标层退役（ADR-0007）
    db_schema: str
    current_sql: str | None
    result: QueryResult | None
    attempts: list[SqlAttempt]
    last_error: str | None
    verify_note: str | None  # verify 判定可疑的原因；None=通过或未经 verify
    precise_candidates: list[str] | None  # M4-C 载荷键（非预算键）：同轮多候选的 SQL 列表；
    # 账本仍由 len(attempts) 承担——批量=一轮，execute 用完即清（整值覆盖语义显式置 None）
    # （M5 matched_metric/metric_note 两键已随指标层退役删除，ADR-0007）
    session_context: dict | None  # M7 票 05 唯一新状态键（precise_candidates 同款载荷纪律：显式键、
    # 整值覆盖）：{"turns": [L2 窗口原文行 {question/sql/row_count/head/failed，时间升序·条数由
    # 票 05 预算窗口决定，K=5 降为默认换算结果]，"draft": L1 {sql/head} 或 None，
    # 摘要在场时加 "digest_lines": [{turn, ts, line}…（行链＋段落行，M9 票 05 滚存摘要链，
    # web 层懒补后装填——渲染归 prompts.format_session_history、淘汰归 gssc 保险丝）]}。
    # 仅 understand/generate 消费（姊妹钉测＝test_generate_never_reads_intent 同款条款，
    # tests/test_session_context.py AST 源扫描钉死其他节点不得读取）；None/缺键＝无会话关态，
    # understand/generate prompt 与现状逐字节一致；无 digest_lines 键＝票 04 现状逐字节一致。
    # L3 全史归档永不进 prompt（预算窗口、摘要欠账与草稿资格在 web 层 build_session_context/
    # catch_up_digest 切好再装填）。
    answer: Answer | None
