"""提示词。纪律：SYSTEM_RULES 永远位于 prompt 最前端（吃前缀缓存，见设计文档优化 #7）。"""

SYSTEM_RULES = """你是一个严谨的数据分析 SQL 专家。规则：
1. 数据库是 SQLite 方言。
2. 只能使用「数据库 Schema」中给出的表和列，禁止编造任何表名或列名。
3. 输出必须是单条 SQL，以 SELECT 或 WITH 开头。
4. 只输出 SQL 本身：不要解释、不要注释、不要 Markdown 代码块标记。
5. 不格式化输出：禁止 printf/round/CAST 改变数值的类型或精度，直接输出原始值；不要拼接 % 等后缀字符。"""

_UNDERSTAND_TMPL = """请把下面的用户问题改写为一句自包含的查询意图：保留原意、补全指代、不要回答问题，直接输出改写后的一句话。
原始问题：{question}"""

_SQL_TMPL = SYSTEM_RULES + """

## 数据库 Schema
{schema}

## 背景信息
{evidence}

## 用户问题
{question}
{history}
输出一条 SQL："""

_RESPOND_TMPL = """你是数据分析助手。请根据查询结果用中文给出一句话结论，并简述数据依据。若结果为空，请如实说明未查询到数据，禁止编造。
## 用户问题
{question}
## 所用 SQL
{sql}
## 查询结果（前 {n} 行，共取到 {total} 行）
{rows_table}
结论："""


def understand_prompt(question: str) -> str:
    return _UNDERSTAND_TMPL.format(question=question)


def sql_prompt(schema: str, evidence: str, question: str, history: str = "") -> str:
    h = "\n" + history if history else ""
    return _SQL_TMPL.format(schema=schema, evidence=evidence or "（无）", question=question, history=h)


def format_failure_history(attempts: list, verify_note: str | None) -> str:
    """失败历史摘要（自纠错上下文工程核心素材）：SQL＋错误首行；空列表返回空串。"""
    if not attempts:
        return ""
    lines = ["## 之前的失败尝试"]
    last = len(attempts) - 1
    for i, a in enumerate(attempts):
        head = f"尝试 {i + 1}：{a.sql}" if a.sql else f"尝试 {i + 1}：（未能提取出合法 SQL）"
        if a.error:
            lines.append(head + f"\n  错误：{a.error.splitlines()[0]}")
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
