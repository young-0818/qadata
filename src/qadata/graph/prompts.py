"""提示词。纪律：SYSTEM_RULES 永远位于 prompt 最前端（吃前缀缓存，见设计文档优化 #7）。"""
from collections.abc import Sequence

from qadata.graph.error_hints import error_hint
from qadata.graph.precise import NO_MAJORITY_ERROR

SYSTEM_RULES = """你是一个严谨的数据分析 SQL 专家。规则：
1. 数据库是 SQLite 方言。
2. 只能使用「数据库 Schema」中给出的表和列，禁止编造任何表名或列名。
3. 输出必须是单条 SQL，以 SELECT 或 WITH 开头。
4. 只输出 SQL 本身：不要解释、不要注释、不要 Markdown 代码块标记。
5. 不字符串化输出：禁止 printf 改变数值的类型；不要拼接 % 等后缀字符；若题面明确要求百分比或小数精度，按题面要求计算并保留（如 *100、保留 N 位小数）。
6. 只选问题需要的列，不要"顺带"返回多余列；除非问题明确要求全部列。"""

# 载体 A（M5 票 02）：改写＋六字段意图同一次调用产出（零新增调用）；意图唯一消费者
# 是 metric_match 填槽（票 05）——generate 尾段注入线判负已拆（④裁决，见 graph/intent.py）。
# 花括号需双写（.format 模板）；「宁空勿造」纪律写在指令里，防模型推断改坏（M4-B v1 教训）。
_UNDERSTAND_TMPL = """请把下面的用户问题改写为一句自包含的查询意图：保留原意、补全指代、不要回答问题。
同时按「宁空勿造」从题面/背景信息中抽取意图字段：每个字段仅当题面或背景信息明示时才填写，否则一律 null，禁止猜测或推断。
只输出一个 JSON 对象，不要 Markdown 代码块、不要解释、不要多余文字。字段契约：
{{"question": "改写后的一句话",
 "intent": {{
  "metric_mention": "问题中指称指标的业务词（如「违约率」），未明示则 null",
  "dimensions": ["题面明示的分组/视角轴（如「按月份」）"],
  "filters": ["题面明示的筛选条件，原样摘录题面措辞词；时间类条件只输出裸时间表达式（如 \"1993\" 而非 \"in 1993\"；另如 \"1993/2\"、\"去年\"），不带介词短语（票 04 裁决⑧：否则填槽的时间解析对英文题面系统性失效）"],
  "output_form": "题面明示的输出形态（如「百分比」「列出全部」「要输出哪几列」）",
  "format_constraint": "题面明示的格式要求（如「保留两位小数」），未明示则 null",
  "evidence_terms": ["背景信息中以「术语 = 定义」形式明示的条目，原样摘录"]}}}}
{history}原始问题：{question}
背景信息（evidence）：{evidence}"""

# M8 票 03 澄清保险丝（默认关；尾追静态段，开关关时 prompt 与今日逐字节一致）。
# 保险丝不是主菜：指令把产出条件收紧到「任何 SQL 都是猜」，并明列不构成澄清的情形
# （值写法有采样/兜底、列归属有 explore、形态缺省可合理选——都轮不到回问）。
# 防循环闸＝「补充说明：」标记：续轮题面必带（合成公式 compose_supplement 单源），
# 带标记＝本轮指令段不加（节点侧另有机制版复闸——M5 ⑨闸教训：指令守不住的，代码兜）。
SUPPLEMENT_MARK = "补充说明："


def compose_supplement(original: str, ask: str, supplement: str) -> str:
    """续轮合成公式（单源：HITL 节点恢复态与 web 轮次归档共用，防两处字面漂移）——
    原问＋「补充说明：」＋澄清问＋答，带标记即满足防循环闸。"""
    return f"{original}{SUPPLEMENT_MARK}{ask} {supplement.strip()}"


_CLARIFY_TAIL = """
澄清例外（保险丝，不是常规出口）：仅当口径缺失到「任何 SQL 都只能是猜」的程度——
题面与背景信息都定不了到底算什么、从哪算，两种以上合理读法会给出不同数字且无从取舍——
才在上述 JSON 中额外追加一个字段 "clarification": "一句中文澄清问（问清缺的是什么，不超过 30 字）"；
其他任何情况一律不写该字段或填 null。以下都**不构成**澄清理由：
取值的具体写法/大小写（按 schema 所示形态处理）、列与表的归属（系统自会探查）、
输出形态或精度未明示（选合理形态作答）、时间范围未给出（按题面处理）。
能改写、能给出合理 SQL 就不要问。"""

# M5 票 05 第二级 LLM 复核（spec「匹配机制」）：整表装入、只判身份、禁写 SQL。
# 模板不进 prompt（防照抄、省 token）；⑨闸为 04→05 移交裁决（04 票单 §F.4：
# 包含路径误命中户均条——具名个体极值/比较题不是聚合口径指标，一律 NONE）。
_METRIC_REVIEW_TMPL = """你是指标口径审查员。下面是人工审定的指标注册表（每行：内部名｜展示名｜业务含义｜口径定义）。
判断用户问题所求的指标是否恰好为其中某一条。只输出选中条目的内部名，或输出 NONE；禁止输出 SQL、解释或多余文字。
规则：
1. 只能选注册表内已有的内部名；题面指标与某条口径不完全等价、或你拿不准 → NONE（宁漏勿错）。
2. 题面求具名个体的极值或比较（lowest / highest / top-N / which one / 「最…的账户/客户/地区」）→ 一律判 NONE。
3. 命中不以题面给出时间/筛选条件为前提——参数填不填得齐由后续填槽裁决，你只判指标身份。
## 指标注册表
{table}
## 背景信息（evidence）
{evidence}
## 用户问题
{question}
你的输出："""


def metric_review_prompt(question: str, evidence: str, metrics: list) -> str:
    """L2 复核 prompt：整表 ≤18 条一次装入（spec 匹配机制第二级）。"""
    table = "\n".join(
        f"- {m.name}｜{m.display_name}｜{m.meaning}｜{m.definition}" for m in metrics
    )
    return _METRIC_REVIEW_TMPL.format(question=question, evidence=evidence or "（无）",
                                      table=table)


_SQL_TMPL = SYSTEM_RULES + """

## 数据库 Schema
{schema}

## 背景信息
{evidence}

## 用户问题
{question}
{history}
输出一条 SQL："""

_RESPOND_TMPL = """你是数据分析助手。请根据查询结果用中文给出一句话结论；只写结论本身，不要解释、不要列数据明细（数据依据与口径由系统另行标注）。若结果为空，请如实说明未查询到数据，禁止编造。
## 用户问题
{question}
## 所用 SQL
{sql}
## 查询结果（前 {n} 行，共取到 {total} 行）
{rows_table}
结论："""


# ── M7 票 05 三层记忆渲染（L2→understand 消解、L1→generate 尾部草稿）──────────
# 零 LLM 确定性拼装；载荷形状契约见 graph/state.py。空块＝关态/无可用记忆，
# prompt 与票 04 现状逐字节一致（专测钉死）。错误草稿不传染：failed 轮只留问题
# 供消解、如实标失败；草稿仅为参考的措辞写进节头，照走完整沙箱＋verify 是代码路径
# 保证（草稿不改路由/沙箱/校验/账本一分）。
# 票 09（owner 裁 2026-09-15）：两个节头带**显式授权**——"与历史/上问无关则忽略"，
# 话题连续性由模型在这两处既有调用内隐式判（Cortex Analyst 同构，零新增调用）；
# 人肉 fresh_topic 闸已撤，判错的代价方向性＝宁多带勿错切（多带的旧史由沙箱/verify
# 兜住，错切真指代直接答错）。

def format_session_history(ctx: dict | None) -> str:
    """L2 情节记忆段（最近 K 轮，web 层已切窗）：问题/SQL/行数/标量头部。"""
    if not isinstance(ctx, dict):
        return ""
    lines: list[str] = []
    for t in ctx.get("turns") or []:
        q = str(t.get("question") or "")
        if t.get("failed"):
            lines.append(f"- 问：{q}（该轮查询失败，无可靠结果与 SQL 可参考）")
            continue
        entry = [f"- 问：{q}"]
        if t.get("sql"):
            entry.append(f"  SQL：{t['sql']}")
        if t.get("row_count") is not None:
            head = t.get("head")
            entry.append(f"  结果：{t['row_count']} 行" + (f"；{head}" if head else ""))
        lines.append("\n".join(entry))
    if not lines:
        return ""
    return ("## 会话历史（按时间升序，最后一条是上一轮；用于消解「这些/那些/它」等指代与延续主体，"
            "不要回答历史里的问题；本轮问题与历史无关时忽略这段历史，独立改写为自包含一句）\n"
            + "\n".join(lines))


def format_session_draft(ctx: dict | None) -> str:
    """L1 工作记忆段（上一轮完整 SQL＋结果头部摘要）——generate 的增量改写草稿。"""
    if not isinstance(ctx, dict):
        return ""
    d = ctx.get("draft")
    sql = str((d or {}).get("sql") or "") if isinstance(d, dict) else ""
    if not sql:
        return ""
    block = ("## 上一轮 SQL（增量改写的草稿：本题若为其延续（如「这些新生」的定义即写在其中），"
             "在其结构上改；它仅供参考、不是本题答案，仍须按本题完整生成；"
             "本题与上一问无关时忽略这段草稿，按本题独立完整生成）\n" + sql)
    head = str(d.get("head") or "") if isinstance(d, dict) else ""
    return block + (f"\n上一轮结果摘要：{head}" if head else "")


def understand_prompt(question: str, evidence: str = "", session_block: str = "",
                      clarify: bool = False) -> str:
    """understand 的完整 prompt。clarify（M8 票 03）＝开关开**且**题面不含「补充说明：」
    标记才尾追澄清指令段——续轮必带标记（防死循环闸的指令侧；节点侧消费复闸在 nodes.understand）。
    关态/带标记态与本函数参数加入前逐字节一致（专测钉死）。"""
    h = session_block + "\n" if session_block else ""
    p = _UNDERSTAND_TMPL.format(question=question, evidence=evidence or "（无）", history=h)
    if clarify and SUPPLEMENT_MARK not in question:
        p += _CLARIFY_TAIL
    return p


def sql_prompt(schema: str, evidence: str, question: str, history: str = "",
               draft: str = "") -> str:
    h = ("\n" + history if history else "") + ("\n" + draft if draft else "")
    return _SQL_TMPL.format(schema=schema, evidence=evidence or "（无）", question=question, history=h)


def format_failure_history(attempts: list, verify_note: str | None,
                           metric_note: str | None = None) -> str:
    """失败历史摘要（自纠错上下文工程核心素材）：SQL＋错误首行；空列表返回空串。

    metric_note（票 05）：模板降级原因非空时先挂一段——generate 需知「上轮走的是指标模板
    且已失败」，避免重蹈同一口径写法（降级后仍进本环，走的是常规 SQL 生成）。"""
    if not attempts:
        return ""
    lines = ["## 之前的失败尝试"]
    if metric_note:
        lines.insert(0, f"## 指标模板降级\n{metric_note}")
    last = len(attempts) - 1
    for i, a in enumerate(attempts):
        if a.sql:
            head = f"尝试 {i + 1}：{a.sql}"
        elif a.error == NO_MAJORITY_ERROR:
            head = f"尝试 {i + 1}：（多候选票决未获多数）"  # 勿误标成提取失败
        else:
            head = f"尝试 {i + 1}：（未能提取出合法 SQL）"
        if a.error:
            line = head + f"\n  错误：{a.error.splitlines()[0]}"
            hint = error_hint(a.error)
            if hint:
                line += f"\n  修复建议：{hint}"
            lines.append(line)
        elif i == last and verify_note:
            lines.append(head + f"\n  错误：上次执行成功但校验未通过——{verify_note}")
        else:
            lines.append(head + "\n  错误：未知")
    return "\n".join(lines)


def strip_conclusion_prefix(text: str) -> str:
    """防御「结论：结论：」复读（respond 模板自带"结论："引导，模型可能照抄）。"""
    t = text.strip()
    while True:
        stripped = t
        for prefix in ("结论：", "结论:"):
            if t.startswith(prefix):
                t = t[len(prefix):].strip()
        if t == stripped:
            return t


def respond_prompt(question: str, sql: str, rows_table: str, total: int, n: int) -> str:
    return _RESPOND_TMPL.format(question=question, sql=sql, rows_table=rows_table, total=total, n=n)


def compose_conclusion(conclusion: str, basis: str = "", caliber: str = "",
                       notes: Sequence[str] = ()) -> str:
    """E1 三节答案组装（票 07，纯函数零 token）：结论 → 数据依据 → 口径说明 → 校验标注。

    只有【结论】来自 LLM，其余各节由状态与注册表确定性派生、原样接入（永不编造）；
    空节省略不空转——校验标注全 None 时连节头都不出现（票 07 条款③）。
    """
    parts = [f"【结论】{conclusion}"]
    if basis:
        parts.append(f"【数据依据】{basis}")
    if caliber:
        parts.append(f"【口径说明】{caliber}")
    if notes:
        parts.append("【校验标注】\n" + "\n".join(f"- {n}" for n in notes))
    return "\n".join(parts)
