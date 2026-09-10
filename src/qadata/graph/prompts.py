"""提示词。纪律：SYSTEM_RULES 永远位于 prompt 最前端（吃前缀缓存，见设计文档优化 #7）。"""
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
原始问题：{question}
背景信息（evidence）：{evidence}"""

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


def understand_prompt(question: str, evidence: str = "") -> str:
    return _UNDERSTAND_TMPL.format(question=question, evidence=evidence or "（无）")


def sql_prompt(schema: str, evidence: str, question: str, history: str = "") -> str:
    h = "\n" + history if history else ""
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
                       notes=()) -> str:
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
