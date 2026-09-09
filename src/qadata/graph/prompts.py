"""提示词。纪律：SYSTEM_RULES 永远位于 prompt 最前端（吃前缀缓存，见设计文档优化 #7）。"""
from qadata.graph.error_hints import error_hint
from qadata.graph.intent import format_intent_constraints
from qadata.graph.precise import NO_MAJORITY_ERROR

SYSTEM_RULES = """你是一个严谨的数据分析 SQL 专家。规则：
1. 数据库是 SQLite 方言。
2. 只能使用「数据库 Schema」中给出的表和列，禁止编造任何表名或列名。
3. 输出必须是单条 SQL，以 SELECT 或 WITH 开头。
4. 只输出 SQL 本身：不要解释、不要注释、不要 Markdown 代码块标记。
5. 不字符串化输出：禁止 printf 改变数值的类型；不要拼接 % 等后缀字符；若题面明确要求百分比或小数精度，按题面要求计算并保留（如 *100、保留 N 位小数）。
6. 只选问题需要的列，不要"顺带"返回多余列；除非问题明确要求全部列。"""

# 载体 A（M5 票 02）：改写＋六字段意图同一次调用产出（零新增调用）。
# 花括号需双写（.format 模板）；「宁空勿造」纪律写在指令里，防模型推断改坏（M4-B v1 教训）。
_UNDERSTAND_TMPL = """请把下面的用户问题改写为一句自包含的查询意图：保留原意、补全指代、不要回答问题。
同时按「宁空勿造」从题面/背景信息中抽取意图字段：每个字段仅当题面或背景信息明示时才填写，否则一律 null，禁止猜测或推断。
只输出一个 JSON 对象，不要 Markdown 代码块、不要解释、不要多余文字。字段契约：
{{"question": "改写后的一句话",
 "intent": {{
  "metric_mention": "问题中指称指标的业务词（如「违约率」），未明示则 null",
  "dimensions": ["题面明示的分组/视角轴（如「按月份」）"],
  "filters": ["题面明示的筛选条件"],
  "output_form": "题面明示的输出形态（如「百分比」「列出全部」「要输出哪几列」）",
  "format_constraint": "题面明示的格式要求（如「保留两位小数」），未明示则 null",
  "evidence_terms": ["背景信息中以「术语 = 定义」形式明示的条目，原样摘录"]}}}}
原始问题：{question}
背景信息（evidence）：{evidence}"""

# {constraints}＝意图三约束字段（output_form/format_constraint/evidence_terms）动态尾段，
# 全空时渲染为空串——与接线前逐字节一致（防污染回归 tests/test_intent.py 钉死）。
_SQL_TMPL = SYSTEM_RULES + """

## 数据库 Schema
{schema}

## 背景信息
{evidence}

## 用户问题
{question}
{constraints}{history}
输出一条 SQL："""

_RESPOND_TMPL = """你是数据分析助手。请根据查询结果用中文给出一句话结论，并简述数据依据。若结果为空，请如实说明未查询到数据，禁止编造。
## 用户问题
{question}
## 所用 SQL
{sql}
## 查询结果（前 {n} 行，共取到 {total} 行）
{rows_table}
结论："""


def understand_prompt(question: str, evidence: str = "") -> str:
    return _UNDERSTAND_TMPL.format(question=question, evidence=evidence or "（无）")


def sql_prompt(schema: str, evidence: str, question: str, history: str = "",
               intent: dict | None = None) -> str:
    h = "\n" + history if history else ""
    return _SQL_TMPL.format(schema=schema, evidence=evidence or "（无）", question=question,
                            constraints=format_intent_constraints(intent), history=h)


def format_failure_history(attempts: list, verify_note: str | None) -> str:
    """失败历史摘要（自纠错上下文工程核心素材）：SQL＋错误首行；空列表返回空串。"""
    if not attempts:
        return ""
    lines = ["## 之前的失败尝试"]
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
