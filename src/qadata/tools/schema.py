"""Schema 探索工具：列出表、取 DDL、采样数据、构建上下文。"""
import sqlite3

FULL_SCHEMA_LIMIT = 8000  # schema 全量超过该字符数才请求 LLM 选表

_PICK_PROMPT = (
    "数据库有如下表：{tables}。用户问题：{question}。"
    "请选出回答该问题最可能相关的表名，用英文逗号分隔，只输出表名："
)


def list_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows]


def get_schema(conn: sqlite3.Connection, table: str) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type IN ('table','view') AND name=?", (table,)
    ).fetchone()
    return row[0] if row and row[0] else f"-- 表 {table} 无 DDL"


def sample_rows(conn: sqlite3.Connection, table: str, n: int = 3) -> str:
    rows = conn.execute(f'SELECT * FROM "{table}" LIMIT {int(n)}').fetchall()
    lines = [f"{table} 示例数据（前 {len(rows)} 行）："]
    lines += [repr(r) for r in rows]
    return "\n".join(lines)


def build_schema_context(
    conn: sqlite3.Connection, question: str, llm=None, max_chars: int = FULL_SCHEMA_LIMIT,
    tracer=None,
) -> str:
    """构建给 LLM 的 schema 上下文：小库全量；大库让 LLM 先选相关表。"""
    tables = list_tables(conn)
    ddls = [get_schema(conn, t) for t in tables]
    full = "\n\n".join(ddls)
    if len(full) <= max_chars or llm is None:
        return full
    picked = _pick_tables_with_llm(llm, tables, question, tracer)
    if picked is None:  # LLM 输出解析失败 → 回退全量（宁可多给不可编造）
        return full
    return "\n\n".join(get_schema(conn, t) for t in picked)


def _pick_tables_with_llm(llm, tables: list[str], question: str, tracer=None) -> list[str] | None:
    # 走 timed_invoke：选表调用也进 tracing（M1 观测盲区修复）
    from qadata.llm.tracing import timed_invoke
    content = timed_invoke(llm, _PICK_PROMPT.format(tables=", ".join(tables), question=question),
                           "explore", tracer)
    names = [w.strip() for w in str(content).split(",")]
    valid = [n for n in names if n in tables]
    return valid if valid else None
