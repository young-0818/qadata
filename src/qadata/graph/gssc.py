"""GSSC 上下文装配流水线（M9 票 03／ADR-0002）——一切进模型内容的唯一出口。

Gather 收齐候选（gather_*＝读状态、格式化素材，节点侧不再伸手拿料）
→ Select 挑选（本票恒等通过；票 06 embedding top-K＋关键词保底在此入住）
→ Structure 分区落位（六分区＝装配器内部分类法、非统一字节顺序——各场景按现状
  字节序落位，ADR-0002 在案；分区骨架重排已被否，勿回锅）
→ Compress 预算压缩（本票恒等通过；票 04 tiktoken 保险丝在此入住——届时的淘汰序
  按 Section.zone 走：先丢最老记忆行、再缩证据值采样、再缩状态失败历史，
  任务与输出分区永不砍）。

本票＝纯结构收编：模板措辞零改动、路由/调用数/账本/prompt 文本逐字节原样。
字节等价验收＝tests/test_gssc.py 五场景「装配器出口 vs 旧路」双跑 diff 零差异；
prompts.py 的旧装配函数（understand_prompt 等）原样保留充当参照实现（oracle）。
长静态块（JSON 契约/审查员规则/报告指令/选表一行）从现有模板 split 派生——字面
单源零重抄（compose_supplement／tool_frame 先例）；短节头脚手架此处重写，漂移由
双跑金标准测兜红。
"""
from enum import Enum
from typing import NamedTuple

from qadata.graph.prompts import (
    _CLARIFY_TAIL,
    _METRIC_REVIEW_TMPL,
    _PICK_PROMPT,
    _RESPOND_TMPL,
    _UNDERSTAND_TMPL,
    SUPPLEMENT_MARK,
    SYSTEM_RULES,
    TRUNCATION_HINT,
    format_failure_history,
    format_session_draft,
    format_session_history,
    metric_table,
)


class Zone(str, Enum):
    """上下文六分区（CONTEXT.md 词汇表；分类法非字节顺序）。"""

    ROLE = "角色与政策"
    TASK = "任务"
    STATE = "状态"
    EVIDENCE = "证据"
    MEMORY = "记忆"
    OUTPUT = "输出"


class Section(NamedTuple):
    """落位后的一段字节切片：自带脚手架（节头等），join 即成品。"""

    zone: Zone
    text: str


# ── 脚手架派生（split 自现有模板，见模块 docstring 单源纪律）────────────
# understand 头块含 {{}} 转义 JSON 契约，.format() 一次解转义即成品文本。
_U_HEAD, _u_tail = _UNDERSTAND_TMPL.split("{history}", 1)
_U_HEAD = _U_HEAD.format()
_U_Q, _u_after = _u_tail.split("{question}", 1)
_U_EV, _ = _u_after.split("{evidence}", 1)

_M_HEAD = _METRIC_REVIEW_TMPL.split("\n## 指标注册表\n", 1)[0]
_R_HEAD = _RESPOND_TMPL.split("\n## 用户问题\n", 1)[0]
_P0, _p1 = _PICK_PROMPT.split("{tables}", 1)
_P1, _P2 = _p1.split("{question}", 1)


# ── Gather：读状态、格式化素材 → 槽位字典 ──────────────────────────────


def gather_understand(state: dict, *, clarify: bool) -> dict[str, str]:
    """understand 候选：L2 会话历史段（指代消解）＋题面＋口径＋澄清尾段。
    澄清尾段闸＝开关开且题面不含「补充说明：」标记（防循环指令侧——与节点侧
    消费复闸同源条件，understand_prompt 旧路同款）。"""
    question = state["question"]
    return {
        "session_block": format_session_history(state.get("session_context")),
        "question": question,
        "evidence": state.get("evidence", ""),
        "clarify_tail": _CLARIFY_TAIL
        if clarify and SUPPLEMENT_MARK not in question else "",
    }


def gather_generate(state: dict) -> dict[str, str]:
    """generate 候选：schema＋口径＋题面＋失败历史（状态）＋L1 草稿（记忆）。"""
    return {
        "schema": state.get("db_schema", ""),
        "evidence": state.get("evidence", ""),
        "question": state["question"],
        "failure_history": format_failure_history(
            state.get("attempts", []), state.get("verify_note"),
            state.get("metric_note")),
        "draft": format_session_draft(state.get("session_context")),
    }


def gather_metric_review(state: dict, metrics: list) -> dict[str, str]:
    """metric_match（L2 复核）候选：整表渲染＋口径＋题面。"""
    return {
        "table": metric_table(metrics),
        "evidence": state.get("evidence", ""),
        "question": state["question"],
    }


def gather_respond(state: dict, *, result, sql: str | None,
                   rows_table: str, n: int) -> dict[str, str]:
    """respond 候选：题面＋所用 SQL＋结果预览表（含截断提示——prompt 素材归
    装配器；行数展示与校验标注另算，节点自持）＋行数计数。"""
    if result.truncated:
        # 截断提示：让模型如实措辞、勿把截断行当全量（仅影响 prompt 呈现，不参与判分）
        rows_table += TRUNCATION_HINT
    return {
        "question": state["question"],
        "sql": sql or "",
        "rows_table": rows_table,
        "total": str(result.row_count),
        "n": str(n),
    }


def gather_schema_pick(tables: list[str], question: str) -> dict[str, str]:
    """explore（大库选表）候选：表清单＋题面。"""
    return {"tables": ", ".join(tables), "question": question}


# ── Select／Compress：本票恒等通过（不撒谎、不造特殊通道）──────────────


def select(scenario: str, slots: dict[str, str]) -> dict[str, str]:
    """挑选阶段：原样通过。票 06（embedding 召回＋hybrid 保底）在此入住——
    届时候选料池（例题库/schema 条目/业务术语）从 slots 进出，默认关＝本函数形状照旧。"""
    return slots


def compress(scenario: str, sections: list[Section]) -> str:
    """预算压缩阶段：拼接即成品（恒等）。票 04（tiktoken 保险丝）在此入住——
    触发时按分区淘汰序删减 sections（永不砍任务与输出分区），缺词表如实炸。"""
    return "".join(s.text for s in sections)


# ── Structure：分区落位（各场景按现状字节序，模板措辞零改动）───────────


def _structure_understand(p: dict[str, str]) -> list[Section]:
    block = p["session_block"]
    return [
        Section(Zone.TASK, _U_HEAD),
        Section(Zone.MEMORY, block + "\n" if block else ""),
        Section(Zone.TASK, _U_Q + p["question"]),
        Section(Zone.EVIDENCE, _U_EV + (p["evidence"] or "（无）")),
        Section(Zone.OUTPUT, p["clarify_tail"]),
    ]


def _structure_generate(p: dict[str, str]) -> list[Section]:
    history, draft = p["failure_history"], p["draft"]
    return [
        Section(Zone.ROLE, SYSTEM_RULES),
        Section(Zone.EVIDENCE, "\n\n## 数据库 Schema\n" + p["schema"]),
        Section(Zone.EVIDENCE, "\n\n## 背景信息\n" + (p["evidence"] or "（无）")),
        Section(Zone.TASK, "\n\n## 用户问题\n" + p["question"] + "\n"),
        # 空节省略＝整段连前导换行一起不进（sql_prompt 的 h 组装逐字节同款）
        Section(Zone.STATE, "\n" + history if history else ""),
        Section(Zone.MEMORY, "\n" + draft if draft else ""),
        Section(Zone.OUTPUT, "\n输出一条 SQL："),
    ]


def _structure_metric_review(p: dict[str, str]) -> list[Section]:
    return [
        Section(Zone.ROLE, _M_HEAD),
        Section(Zone.EVIDENCE, "\n## 指标注册表\n" + p["table"]),
        Section(Zone.EVIDENCE, "\n## 背景信息（evidence）\n" + (p["evidence"] or "（无）")),
        Section(Zone.TASK, "\n## 用户问题\n" + p["question"]),
        Section(Zone.OUTPUT, "\n你的输出："),
    ]


def _structure_respond(p: dict[str, str]) -> list[Section]:
    return [
        Section(Zone.ROLE, _R_HEAD),
        Section(Zone.TASK, "\n## 用户问题\n" + p["question"]),
        Section(Zone.STATE, "\n## 所用 SQL\n" + p["sql"]),
        Section(Zone.EVIDENCE,
                f"\n## 查询结果（前 {p['n']} 行，共取到 {p['total']} 行）\n"
                + p["rows_table"]),
        Section(Zone.OUTPUT, "\n结论："),
    ]


def _structure_schema_pick(p: dict[str, str]) -> list[Section]:
    return [
        Section(Zone.EVIDENCE, _P0 + p["tables"]),
        Section(Zone.TASK, _P1 + p["question"]),
        Section(Zone.OUTPUT, _P2),
    ]


_STRUCTURE = {
    "understand": _structure_understand,
    "generate": _structure_generate,
    "metric_match": _structure_metric_review,
    "respond": _structure_respond,
    "explore": _structure_schema_pick,
}


def structure(scenario: str, slots: dict[str, str]) -> list[Section]:
    try:
        build = _STRUCTURE[scenario]
    except KeyError:
        raise KeyError(f"未登记的 prompt 场景：{scenario!r}——收编出口只认五场景") from None
    return build(slots)


# ── 唯一出口 ──────────────────────────────────────────────────────────


def assemble(scenario: str, slots: dict[str, str]) -> str:
    """跑完 Select→Structure→Compress，产出进模型的最终 prompt。"""
    return compress(scenario, structure(scenario, select(scenario, slots)))
