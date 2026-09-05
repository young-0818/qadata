"""Schema 探索工具：列出表、取 DDL、采样数据、构建上下文（M4 值感知关联）。

M4-B：schema 上下文在 M3 描述注入管线上升级为三段——
  列注释（database_description 的 column_description）
  取值参考（官方 value_description 为主，文本列缺失才 DISTINCT 采样兜底）
  关联提示（*_id / link_to_X 列名推断，低成本逻辑模型）
"""
import csv
import sqlite3
from pathlib import Path

FULL_SCHEMA_LIMIT = 8000  # schema 全量超过该字符数才请求 LLM 选表
VALUES_PER_COL = 15       # 每列取值上限（spec §3.3 控 token）
VALUES_SECTION_CAP = 1500  # 每表取值参考段字符上限，超则截断加「…」
_TEXT_MARKERS = ("text", "char", "varchar", "clob")

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


def _load_description_rows(db_path: str | None, table: str) -> list[dict]:
    """读 database_description/{table}.csv 为行列表（BIRD 官方格式，表头自适应）；
    无文件/读失败返回空列表（锦上添花不连累本体）。"""
    if not db_path:
        return []
    csv_path = Path(db_path).parent / "database_description" / f"{table}.csv"
    if not csv_path.exists():
        return []
    try:
        # utf-8-sig 容 BOM；errors="replace" 容 BIRD 部分非 UTF-8 字节（如 formula_1 的
        # 0x96）——宁可以替换符保留大部分注释，也不让编码错连累 explore（M3 回归）
        with csv_path.open(encoding="utf-8-sig", errors="replace") as f:
            return list(csv.DictReader(f))
    except (OSError, csv.Error):
        return []


def _description_section(db_path: str | None, table: str) -> str:
    """表 X 列注释：- col：column_description（M3 格式；取值说明移至取值参考段）。"""
    lines = []
    for r in _load_description_rows(db_path, table):
        col = (r.get("column_name") or "").strip()
        desc = (r.get("column_description") or "").strip()
        if col and desc:
            lines.append(f"- {col}：{desc}")
    if not lines:
        return ""
    return f"表 {table} 列注释：\n" + "\n".join(lines)


def _is_text_type(col_type: str) -> bool:
    t = (col_type or "").lower()
    return any(m in t for m in _TEXT_MARKERS)


def _sample_column_values(db_path: str | None, table: str, column: str) -> list[str] | None:
    """文本筛选列 CSV 取值缺失时的兜底采样（SELECT DISTINCT LIMIT 15）。

    复用 execute_sql 的只读连接＋sqlglot 白名单＋5s 超时闸；任何失败（含超时、
    校验错、无值）静默跳过该列——兜底稀有触发（主要靠官方 value_description），
    生产大库才依赖（spec §3.3）。"""
    if not db_path:
        return None

    def _q(name: str) -> str:
        return '"' + name.replace('"', '""') + '"'

    from qadata.tools.executor import execute_sql  # 局部导入：executor 反向依赖本模块
    try:
        res = execute_sql(
            db_path,
            f"SELECT DISTINCT {_q(column)} FROM {_q(table)} LIMIT {VALUES_PER_COL}",
            max_rows=VALUES_PER_COL, timeout_s=5.0,
        )
        vals = [str(r[0]) for r in res.rows if r[0] is not None]
        return vals if vals else None
    except Exception:  # noqa: BLE001 兜底是锦上添花：超时/校验错一律跳过该列，不连累 explore
        return None


def _values_section(conn: sqlite3.Connection, db_path: str | None, table: str) -> str:
    """表 X 取值参考：官方 value_description 为主，文本列取值缺失才 DISTINCT 兜底；
    每列 ≤15 值，整段超 VALUES_SECTION_CAP 截断（控 token）。"""
    csv_values = {}
    for r in _load_description_rows(db_path, table):
        col = (r.get("column_name") or "").strip()
        vd = (r.get("value_description") or "").strip()
        if col and vd:
            csv_values[col] = vd
    lines = []
    budget = VALUES_SECTION_CAP
    # PRAGMA table_info 列序：cid, name, type, notnull, dflt_value, pk
    for _cid, col, col_type, *_ in conn.execute(f'PRAGMA table_info("{table}")'):
        text = None
        if col in csv_values:
            text = csv_values[col]
        elif _is_text_type(col_type):
            vals = _sample_column_values(db_path, table, col)
            if vals:
                text = " | ".join(vals)
        if not text:
            continue
        line = f"- {col}：{text}"
        if len(line) > budget:
            lines.append((line[:budget] + "…") if budget > 20 else "…")
            break
        budget -= len(line)
        lines.append(line)
    if not lines:
        return ""
    return f"表 {table} 取值参考：\n" + "\n".join(lines)


def _fk_hints(conn: sqlite3.Connection, tables: list[str]) -> str:
    """*_id / link_to_X 列名推断关联提示（spec §2「逻辑数据模型」低成本归并项）：
    列 account_id → 存在表 account；列 link_to_member → 存在表 member。
    无同名表/自引用形态不提示，避免误导。"""
    lower_map = {t.lower(): t for t in tables}
    hints = []
    for t in tables:
        try:
            cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{t}")')]
        except sqlite3.Error:
            continue
        for c in cols:
            cl = c.lower()
            if cl.endswith("_id"):
                base = c[:-3]
            elif cl.startswith("link_to_"):  # student_club 实库形态：link_to_member
                base = c[len("link_to_"):]
            else:
                continue
            base = base.strip()
            ref = lower_map.get(base.lower())
            if ref and ref != t:
                hints.append(f"- 表 {t} 列 {c} 疑似引用表 {ref} 的外键（JOIN 时注意）")
    return "关联提示：\n" + "\n".join(hints) if hints else ""


def build_schema_context(
    conn: sqlite3.Connection, question: str, llm=None, max_chars: int = FULL_SCHEMA_LIMIT,
    tracer=None, db_path: str | None = None, limiter=None,
) -> str:
    """构建给 LLM 的 schema 上下文：小库全量；大库让 LLM 先选相关表。

    选表判断只用 DDL＋列注释（便宜）；取值参考段只给最终选中的表采样（大库省
    DISTINCT 开销）；关联提示段追加在末尾（体积可忽略，不计入选表阈值）。"""

    def _ctx(names: list[str], with_values: bool) -> str:
        parts = [get_schema(conn, t) for t in names]
        parts += [_description_section(db_path, t) for t in names]
        if with_values:
            parts += [_values_section(conn, db_path, t) for t in names]
        return "\n\n".join(p for p in parts if p)

    tables = list_tables(conn)
    full = _ctx(tables, with_values=False)
    if len(full) <= max_chars or llm is None:
        return _join_ctx(_ctx(tables, with_values=True), _fk_hints(conn, tables))
    picked = _pick_tables_with_llm(llm, tables, question, tracer, limiter)
    if picked is None:  # LLM 输出解析失败 → 回退全量（宁可多给不可编造）
        return _join_ctx(_ctx(tables, with_values=True), _fk_hints(conn, tables))
    return _join_ctx(_ctx(picked, with_values=True), _fk_hints(conn, picked))


def _join_ctx(body: str, hints: str) -> str:
    return body + ("\n\n" + hints if hints else "")


def _pick_tables_with_llm(llm, tables: list[str], question: str, tracer=None,
                          limiter=None) -> list[str] | None:
    # 走 timed_invoke：选表调用也进 tracing（M1 观测盲区修复）
    from qadata.llm.tracing import timed_invoke
    content = timed_invoke(llm, _PICK_PROMPT.format(tables=", ".join(tables), question=question),
                           "explore", tracer, limiter)
    names = [w.strip() for w in str(content).split(",")]
    valid = [n for n in names if n in tables]
    return valid if valid else None
