"""GSSC 上下文装配流水线（M9 票 03／ADR-0002）——一切进模型内容的唯一出口。

Gather 收齐候选（gather_*＝读状态、格式化素材，节点侧不再伸手拿料）
→ Select 挑选（本票恒等通过；票 06 embedding top-K＋关键词保底在此入住）
→ Structure 分区落位（六分区＝装配器内部分类法、非统一字节顺序——各场景按现状
  字节序落位，ADR-0002 在案；分区骨架重排已被否，勿回锅）
→ Compress 预算压缩（M9 票 04 tiktoken 保险丝在此入住，见下方 FUSE 段）。

本票＝纯结构收编：模板措辞零改动、路由/调用数/账本/prompt 文本逐字节原样。
字节等价验收＝tests/test_gssc.py 五场景「装配器出口 vs 旧路」双跑 diff 零差异；
prompts.py 的旧装配函数（understand_prompt 等）原样保留充当参照实现（oracle）。
长静态块（JSON 契约/审查员规则/报告指令/选表一行）从现有模板 split 派生——字面
单源零重抄（compose_supplement／tool_frame 先例）；短节头脚手架此处重写，漂移由
双跑金标准测兜红。
"""
from enum import Enum
from pathlib import Path
from typing import NamedTuple

from qadata.graph.prompts import (
    _CLARIFY_TAIL,
    _METRIC_REVIEW_TMPL,
    _PICK_PROMPT,
    _RESPOND_TMPL,
    _UNDERSTAND_TMPL,
    DIGEST_LINE_HEADER,
    DIGEST_PARA_HEADER,
    FAILURE_HISTORY_HEADER,
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


# ── Compress 总闸：tiktoken 预算保险丝（M9 票 04，spec §二 Q3／ADR-0002）──────
# 计量＝tiktoken cl100k_base（词表文件＝仓库硬资产 src/qadata/assets/，无网络兜底、
# 缺失如实炸，config.load_settings 启动即查）；事后真值审计照旧走 API usage 账本
# （traces.jsonl，与本闸无关——此闸纯前置计量）。
# 阈值＝账本（runs/traces.jsonl 全量历史）各场景实测最大 input_tokens × 安全系数 3，
# 定阈证据与单位保守性判据落档 .scratch/qadata-m9/ticket04-threshold.md——方向保守：
# cl100k 对中文计量粒度比订阅端词表更细（同文计数只高不低），真载荷距闸更远。
# 评测形态永不触发＝行为零变化；触发只可能出现在生产长会话/巨 schema 形态。
# 淘汰序（资料类只有程序硬砍，spec §五 勿回锅）：先丢最老窗口原文行、次摘要行、
# 再纪要段（M9 票 05 接线＝先丢行、后丢段——密度越高越守得住），次砍值采样、
# 再缩失败历史——按 Zone 优先级逐级、确定性、零 LLM；任务与输出分区永不砍，
# 无料可砍的分区（respond 结果表、explore 表清单等）如实入账不硬砍。
FUSE_TOKENS: dict[str, int] = {
    "understand": 4293,    # 账本最大 1431 × 3
    "generate": 15645,     # 5215 × 3
    "metric_match": 3954,  # 1318 × 3
    "respond": 17628,      # 5876 × 3
    "explore": 522,        # 174 × 3
}

VOCAB_PATH = Path(__file__).resolve().parent.parent / "assets" / "cl100k_base.tiktoken"
# 抄自 tiktoken tiktoken_ext/openai_public.py 的 cl100k_base 定义（历史编码、冻结不改）；
# special_tokens 不装载——prompt 是纯文本，`<|...|>` 样字样按普通字节计量。
_CL100K_PAT_STR = (r"(?i:[sdmt]|ll|ve|re)|[^\r\n\p{L}\p{N}]?+\p{L}++|\p{N}{1,3}+"
                   r"| ?[^\s\p{L}\p{N}]++[\r\n]*+|\s++$|\s*[\r\n]|\s+(?!\S)|\s")

_ENCODING = None


def check_vocab() -> None:
    """词表硬资产在位闸（load_settings 启动时调用）：缺了如实炸，无 len 兜底。"""
    if not VOCAB_PATH.is_file():
        raise RuntimeError(
            f"tiktoken 词表文件缺失（仓库硬资产、无网络兜底）：{VOCAB_PATH}")


def _encoding():
    global _ENCODING
    if _ENCODING is None:
        check_vocab()
        import tiktoken
        from tiktoken.load import load_tiktoken_bpe
        _ENCODING = tiktoken.Encoding(
            name="cl100k_base", pat_str=_CL100K_PAT_STR,
            mergeable_ranks=load_tiktoken_bpe(str(VOCAB_PATH)), special_tokens={})
    return _ENCODING


def count_tokens(text: str) -> int:
    """前置计量（纯函数）：disallowed_special=() ＝特殊记号样字样不拒炸、按普通文本。"""
    return len(_encoding().encode(text, disallowed_special=()))


def fuse_ledger_fields(info: dict) -> dict:
    """保险丝事件 → 审计账本载荷（键名单源：graph 节点与 explore 两个挂点共用）。"""
    return {"actions": "、".join(info["actions"]),
            "tokens_before": info["before"], "tokens_after": info["after"]}


def _drop_oldest_entry(text: str, prefix: str) -> str:
    """删最老一个正文块（prefix 起手的行＋其两空格缩进续行），节头原位保留。
    无可删＝原样返回（调用方以文本不变判断淘汰到头）。"""
    lines = text.split("\n")
    start = next((i for i, ln in enumerate(lines) if ln.startswith(prefix)), None)
    if start is None:
        return text
    end = start + 1
    while end < len(lines) and lines[end].startswith("  "):
        end += 1
    return "\n".join(lines[:start] + lines[end:])


def _evict_memory(sections: list[Section]) -> str | None:
    """第一级：丢最老窗口原文行（"- 问："块＋其缩进的 SQL/结果续行）。行链/纪要段
    各有自己的两级（票 05 接线＝先丢行、后丢段，按节头定位、不碰彼此）；
    无块可删且无摘要节头（generate 的 L1 草稿＝单一参考段）＝整段撤下，
    行全部丢光后节头随之。"""
    for i, s in enumerate(sections):
        if s.zone is not Zone.MEMORY or not s.text.strip():
            continue
        new = _drop_oldest_entry(s.text, "- 问：")
        if new != s.text:
            sections[i] = s._replace(text=new)
            return "丢最老记忆行"
        if DIGEST_LINE_HEADER in s.text or DIGEST_PARA_HEADER in s.text:
            continue  # 窗口行已丢光、摘要料还在——交摘要行/纪要段两级按序接手
        sections[i] = s._replace(text="")
        return "整段撤记忆"
    return None


def _evict_digest(sections: list[Section], header: str, action: str) -> str | None:
    """摘要行/纪要段两级（M9 票 05）：按节头定位所属段、段内丢最老一行。
    条目皆单行（「第N轮：」「第a-b轮：」前缀＝prompts 严格拼装产物、无缩进续行）；
    段内清空＝节头随之。"""
    for i, s in enumerate(sections):
        if s.zone is not Zone.MEMORY or header not in s.text:
            continue
        lines = s.text.split("\n")
        hi = next(j for j, ln in enumerate(lines) if ln.startswith(header))
        j = hi + 1
        while j < len(lines) and not lines[j].startswith("## "):
            j += 1
        k = next((x for x in range(hi + 1, j) if lines[x].startswith("- ")), None)
        if k is None:  # 满段无行＝不该发生；节头清走不留空面
            del lines[hi:j]
        else:
            del lines[k]
        sections[i] = s._replace(text="\n".join(lines))
        return action
    return None


def _cut_value_samples(sections: list[Section]) -> str | None:
    """第二级：砍值采样＝整块切除（确定性一刀；标记头＝schema.py 单源字面，
    惰性 import 防环，同 timed_invoke 姿势）。"""
    from qadata.tools.schema import VALUE_SAMPLE_HEADER
    for i, s in enumerate(sections):
        if s.zone is Zone.EVIDENCE and VALUE_SAMPLE_HEADER in s.text:
            sections[i] = s._replace(text=s.text.split(VALUE_SAMPLE_HEADER, 1)[0])
            return "砍值采样"
    return None


def _shrink_failure_history(sections: list[Section]) -> str | None:
    """第三级：缩失败历史＝删最老"尝试 "块。**结构闸**：只进含 FAILURE_HISTORY_HEADER
    的 STATE 段——respond 的「所用 SQL」同住 STATE 分区但无此后缀料，"SQL 永不砍"
    不靠行首样式运气（双轴评审追补）。"""
    for i, s in enumerate(sections):
        if s.zone is not Zone.STATE or FAILURE_HISTORY_HEADER not in s.text:
            continue
        new = _drop_oldest_entry(s.text, "尝试 ")
        if new != s.text:
            sections[i] = s._replace(text=new)
            return "缩失败历史"
    return None


def compress(scenario: str, sections: list[Section], *, on_compress=None) -> str:
    """预算压缩阶段：限内＝恒等拼接（逐字节零变化）；超限＝按 Zone 优先级确定性淘汰
    （窗口原文行→摘要行→纪要段→值采样→失败历史，任务/输出永不砍），每步一单位
    直至限内或无料可砍。
    on_compress（降级入账回调）仅在真压缩时触发一次——账本＋直播帧流两出口由挂点自持。"""
    budget = FUSE_TOKENS[scenario]
    cur = "".join(s.text for s in sections)
    before = tokens = count_tokens(cur)
    if tokens <= budget:
        return cur
    # ponytail: 每步全量重计＝O(步数×全文)——淘汰步数个位数、编码亚毫秒级，
    # 升级判据＝压缩路径耗时在 trace 上可见再改增量计量
    sections = list(sections)  # 淘汰在副本上做，不回馈调用方的原 list
    actions: list[str] = []
    while tokens > budget:
        action = (_evict_memory(sections)
                  or _evict_digest(sections, DIGEST_LINE_HEADER, "丢最老摘要行")
                  or _evict_digest(sections, DIGEST_PARA_HEADER, "丢最老纪要段")
                  or _cut_value_samples(sections) or _shrink_failure_history(sections))
        if action is None:
            break
        actions.append(action)
        cur = "".join(s.text for s in sections)
        tokens = count_tokens(cur)
    if on_compress is not None:
        on_compress({"before": before, "after": tokens, "actions": actions})
    return cur


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


def assemble(scenario: str, slots: dict[str, str], *, on_compress=None) -> str:
    """跑完 Select→Structure→Compress，产出进模型的最终 prompt。
    on_compress（票 04）＝保险丝降级入账回调（仅在真压缩时触发），缺省 None＝只压缩不入账。"""
    return compress(scenario, structure(scenario, select(scenario, slots)),
                    on_compress=on_compress)
