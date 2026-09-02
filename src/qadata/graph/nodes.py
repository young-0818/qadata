"""图节点：understand → explore → generate → execute → respond（M1 线性，无自纠错）。"""
import re
import sqlite3

from qadata.llm.tracing import timed_invoke
from qadata.tools.executor import execute_sql
from qadata.tools.schema import build_schema_context
from qadata.graph.prompts import respond_prompt, sql_prompt, understand_prompt
from qadata.types import Answer, QueryResult, SqlAttempt

_FENCE_RE = re.compile(r"```[a-zA-Z]*\n(.*?)```", re.DOTALL)


def extract_sql(text: str) -> str:
    """剥掉 Markdown 围栏取 SQL；提取不到抛 ValueError。"""
    m = _FENCE_RE.search(text)
    raw = m.group(1) if m else text
    sql = raw.strip().strip(";").strip()
    if not sql or not sql.upper().startswith(("SELECT", "WITH")):
        raise ValueError(f"回复中未找到合法 SQL：{text[:80]!r}")
    return sql


def format_rows(result: QueryResult, limit: int = 10) -> str:
    rows = result.rows[:limit]
    head = " | ".join(result.columns)
    body = "\n".join(" | ".join(str(v) for v in r) for r in rows)
    return f"{head}\n{body}" if body else head


def make_nodes(llm, tracer=None):
    """节点工厂：闭包注入 llm 与 tracer，便于测试时替换假模型。"""

    def understand(state: dict) -> dict:
        q = timed_invoke(llm, understand_prompt(state["question"]), "understand", tracer)
        return {"question": str(q).strip()}

    def explore(state: dict) -> dict:
        conn = sqlite3.connect(f"file:{state['db_path']}?mode=ro", uri=True)
        try:
            ctx = build_schema_context(conn, state["question"], llm=llm)
        finally:
            conn.close()
        return {"db_schema": ctx}

    def generate(state: dict) -> dict:
        prompt = sql_prompt(
            schema=state.get("db_schema", ""),
            evidence=state.get("evidence", ""),
            question=state["question"],
        )
        text = timed_invoke(llm, prompt, "generate", tracer)
        try:
            sql = extract_sql(str(text))
        except ValueError as e:
            return {"current_sql": None, "last_error": str(e)}
        return {"current_sql": sql, "last_error": None}

    def execute(state: dict) -> dict:
        sql = state.get("current_sql")
        if not sql:
            return {}  # generate 阶段已失败，直接进入 respond 兜底
        attempts = list(state.get("attempts", []))
        try:
            res = execute_sql(state["db_path"], sql, max_rows=50)
        except Exception as e:  # M1：记录失败并兜底；M2 改为走自纠错条件边
            msg = str(e)
            attempts.append(SqlAttempt(sql=sql, error=msg))
            return {"attempts": attempts, "result": None, "last_error": msg}
        attempts.append(SqlAttempt(sql=sql, row_count=res.row_count))
        return {"attempts": attempts, "result": res, "last_error": None}

    def respond(state: dict) -> dict:
        res = state.get("result")
        sql = state.get("current_sql")
        last_error = state.get("last_error")
        if res is None:
            # 永不编造：失败路径不调 LLM，规则化诚实说明
            return {
                "answer": Answer(
                    conclusion=f"未能完成查询：{last_error or '未知错误'}",
                    sql=sql,
                    result=None,
                    failed=True,
                    error_summary=last_error,
                )
            }
        text = timed_invoke(
            llm,
            respond_prompt(
                question=state["question"],
                sql=sql or "",
                rows_table=format_rows(res),
                total=res.row_count,
                n=len(res.rows[:10]),
            ),
            "respond",
            tracer,
        )
        return {"answer": Answer(conclusion=str(text).strip(), sql=sql, result=res, failed=False)}

    return {
        "understand": understand,
        "explore": explore,
        "generate": generate,
        "execute": execute,
        "respond": respond,
    }
