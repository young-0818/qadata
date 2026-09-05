"""Schema 探索工具：列出表、取 DDL、采样数据、构建上下文。"""
import csv
import sqlite3
from pathlib import Path

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


def _load_description(db_path: str | None, table: str) -> str:
    """读 database_description/{table}.csv（BIRD 官方列注释，表头自适应）；
    无文件/读失败静默返回空串（锦上添花不连累本体）。"""
    if not db_path:
        return ""
    csv_path = Path(db_path).parent / "database_description" / f"{table}.csv"
    if not csv_path.exists():
        return ""
    try:
        # utf-8-sig 容 BOM；errors="replace" 容 BIRD 部分非 UTF-8 字节（如 formula_1 的
        # 0x96）——宁可以替换符保留大部分注释，也不让编码错连累 explore（M3 回归）
        with csv_path.open(encoding="utf-8-sig", errors="replace") as f:
            rows = list(csv.DictReader(f))
    except (OSError, csv.Error):
        return ""
    lines = []
    for r in rows:
        col = (r.get("column_name") or "").strip()
        parts = [(r.get("column_description") or "").strip(),
                 (r.get("value_description") or "").strip()]
        parts = [p for p in parts if p]
        if col and parts:
            lines.append(f"- {col}：{'｜'.join(parts)}")
    if not lines:
        return ""
    return f"表 {table} 列注释：\n" + "\n".join(lines)


def build_schema_context(
    conn: sqlite3.Connection, question: str, llm=None, max_chars: int = FULL_SCHEMA_LIMIT,
    tracer=None, db_path: str | None = None, limiter=None,
) -> str:
    """构建给 LLM 的 schema 上下文：小库全量；大库让 LLM 先选相关表。
    db_path 提供时，附带选中表的 database_description 列注释（M3 #10）。"""

    def _ctx(names: list[str]) -> str:
        parts = [get_schema(conn, t) for t in names]
        parts += [_load_description(db_path, t) for t in names]
        return "\n\n".join(p for p in parts if p)

    tables = list_tables(conn)
    full = _ctx(tables)
    if len(full) <= max_chars or llm is None:
        return full
    picked = _pick_tables_with_llm(llm, tables, question, tracer, limiter)
    if picked is None:  # LLM 输出解析失败 → 回退全量（宁可多给不可编造）
        return full
    return _ctx(picked)


def _pick_tables_with_llm(llm, tables: list[str], question: str, tracer=None,
                          limiter=None) -> list[str] | None:
    # 走 timed_invoke：选表调用也进 tracing（M1 观测盲区修复）
    from qadata.llm.tracing import timed_invoke
    content = timed_invoke(llm, _PICK_PROMPT.format(tables=", ".join(tables), question=question),
                           "explore", tracer, limiter)
    names = [w.strip() for w in str(content).split(",")]
    valid = [n for n in names if n in tables]
    return valid if valid else None
