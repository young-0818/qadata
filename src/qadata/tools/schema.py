"""Schema 探索工具：列出表、取 DDL、采样数据、构建上下文。"""
import csv
import sqlite3
import time
from pathlib import Path

FULL_SCHEMA_LIMIT = 8000  # schema 全量超过该字符数才请求 LLM 选表

# ── M8 票 02 值采样：预算常数（放宽＝改票面） ──
_VALUE_COLS_PER_TABLE = 8      # 每表最多采样的文本列数
_VALUE_PER_COL = 10            # 逐列枚举值上限（取满 +1 判高基数）
_VALUE_CELL_CHARS = 30         # 单值最多字符（超出截断）
_VALUE_TOTAL_CHARS = 2000      # 全局样本块字符预算（确定性裁尾＝表序×PRAGMA 列序）
_VALUE_SCAN_WINDOW = 2000      # DISTINCT 前有界子查询（实施工艺改判①）：裸 DISTINCT
                               # 在百万行表全扫（真库冒烟 14s/库不可接受，explore 被拖垮）。
                               # 2000＝看见存储形态绰绰有余（ISO 日期/大小写/常见枚举全现身，
                               # 大库实测 50k 窗仍 6.8s、2k 窗进毫秒带）；成本 O(window) 且
                               # **确定**——同库双跑同文评测可复现，截断点不由 wall-clock 决定。
                               # 窗口外罕见枚举值可能漏，但采样目的＝形态与非全量清单，如实漏。
_VALUE_QUERY_TIMEOUT_S = 2.0   # 单查询兜底超时（视图背后巨型 join 时仍拦得住；中断＝静默跳列）
# 样本块节头（M9 票 04：保险丝砍值采样按此定位整块边界——单源字面，gssc 惰性 import 共读）
VALUE_SAMPLE_HEADER = "列取值样本（库实际存储形态，WHERE 字面量以此为准）："
# 采样候选声明类型（sqlite 亲和性粗筛；BLOB/数值列枚举值对 WHERE 字面量病灶无益）
_SAMPLE_TYPE_KEYS = ("CHAR", "CLOB", "TEXT", "DATE", "TIME")


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


def _clip(val: object, cell_chars: int = _VALUE_CELL_CHARS) -> str:
    s = str(val)
    return s if len(s) <= cell_chars else s[:cell_chars] + "…"


def _query_with_budget(conn: sqlite3.Connection, sql: str, budget_s: float) -> list[tuple]:
    """列级查询预算：progress handler 超时中断（executor 沙箱同款机制的只读内用）。
    中断/错误抛 OperationalError 由调用方静默吞——采样面整条纪律＝锦上添花不连累本体。"""
    deadline = time.perf_counter() + budget_s

    def _over() -> int:
        return 1 if time.perf_counter() > deadline else 0

    conn.set_progress_handler(_over, 1000)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.set_progress_handler(None, 0)


def _column_sample_line(conn: sqlite3.Connection, table: str, col: str, *,
                        per_col: int, budget_s: float,
                        scan_window: int = _VALUE_SCAN_WINDOW) -> str:
    """一列一行样本。**有界窗口 DISTINCT**（先 LIMIT scan_window 再 DISTINCT——
    百万行大表裸 DISTINCT 全扫实测 14s/库拖垮 explore，不可接受；窗口把每列成本压到
    O(window) 且确定，同库双跑同文＝评测可复现）。取满 per_col+1 行判高基数。

    高基数列**不再分日期/非日期**（实施工艺改判②）：真库冒烟实锤 district.A4 区名混内容
    列被日期正则误判注出无意义范围——统一注「存储形态示例（前 N 值，窗口内）」，让模型
    看见实际书写形态（ISO 日期、大小写、带不带空格）即可，不冒充全量。低基数直接全枚举。
    表/列名源自 PRAGMA 非题面，直查只读 conn 有 sample_rows 先例同信任域。"""
    try:
        vals = [r[0] for r in _query_with_budget(
            conn, f'SELECT DISTINCT "{col}" FROM '
                  f'(SELECT "{col}" FROM "{table}" WHERE "{col}" IS NOT NULL '
                  f'LIMIT {int(scan_window)}) ORDER BY 1 LIMIT {per_col + 1}', budget_s)]
    except sqlite3.Error:
        return ""
    if not vals:
        return ""
    if len(vals) > per_col:  # 撞顶＝窗口内仍高基数：给形态示例（前 3 值），不谎称全枚举
        shown = "｜".join(f"'{_clip(v)}'" for v in vals[:3])
        return f"- {col}：高基数（窗口内），存储形态示例 {shown}"
    shown = "｜".join(f"'{_clip(v)}'" for v in vals[:per_col])
    return f"- {col}：{shown}"


def column_value_samples(
    conn: sqlite3.Connection, tables: list[str], *,
    cols_per_table: int = _VALUE_COLS_PER_TABLE, per_col: int = _VALUE_PER_COL,
    cell_chars: int = _VALUE_CELL_CHARS, total_chars: int = _VALUE_TOTAL_CHARS,
    budget_s: float = _VALUE_QUERY_TIMEOUT_S,
) -> str:
    """M8 票 02 值采样注入——治「值域不可见」病灶（大小写/拼写变体/空格格式/日期
    字面量形态：模型看不见库实际存储，WHERE 就靠猜；M4 终局＋m7 考卷在案靶 ≈9）。

    文本亲和列（声明类型含 CHAR/CLOB/TEXT/DATE/TIME）逐列采样；预算：每表
    ≤cols_per_table 列、每列 ≤per_col 值×≤cell_chars 字、全局 ≤total_chars，
    **确定性裁尾＝表序×PRAGMA 列序整列进出**（无哈希随机，双跑必同文）。
    一切失败（超时/锁/坏列/视图背后可有可无的表）静默跳列。返回 ""＝无可采样。
    """
    lines: list[str] = []
    used = 0
    budget_hit = False
    for table in tables:
        if budget_hit:
            break
        try:
            info = _query_with_budget(conn, f'PRAGMA table_info("{table}")', budget_s)
        except sqlite3.Error:
            continue
        emitted = 0
        for col, decl in ((r[1], str(r[2] or "").upper()) for r in info):
            if emitted >= cols_per_table:
                break
            if not any(k in decl for k in _SAMPLE_TYPE_KEYS):
                continue
            line = _column_sample_line(conn, table, col, per_col=per_col, budget_s=budget_s)
            if not line:
                continue
            if used + len(line) + 1 > total_chars:
                budget_hit = True  # 全局预算＝确定性全停（表序×列序后续整列不进，不回补）
                break
            lines.append(f"[{table}] {line}")
            used += len(line) + 1
            emitted += 1
    if not lines:
        return ""
    return ("列取值样本（库实际存储形态，WHERE 字面量以此为准）：\n" + "\n".join(lines))


def build_schema_context(
    conn: sqlite3.Connection, question: str, llm=None, max_chars: int = FULL_SCHEMA_LIMIT,
    tracer=None, db_path: str | None = None, limiter=None, sample_values: bool = False,
    on_event=None, sink=None,
) -> str:
    """构建给 LLM 的 schema 上下文：小库全量；大库让 LLM 先选相关表。
    db_path 提供时，附带选中表的 database_description 列注释（M3 #10）。
    sample_values＝票 02 值采样开关（对最终入选表追加样本块；False＝零查询
    零文本与现状逐字节一致——专测钉，关态不探一条 DISTINCT）。
    on_event/sink（M8 票 06 帧喂厚）＝explore 子步骤发 kind:"tool" 帧
    （list_tables/get_schema/select_tables/value_samples），选表 LLM 调用的 token
    经 sink 流进该步结果帧；缺省 None 零发、与现状逐行为一致。"""
    from qadata.llm.tracing import tool_frame  # 帧形单源（timed_invoke 同款惰性路）

    def _tool(name: str, t0: float, ok: bool = True) -> None:
        if on_event is not None:
            on_event(tool_frame("explore", name, t0, ok))

    def _ctx(names: list[str]) -> str:
        t0 = time.perf_counter()
        parts = [get_schema(conn, t) for t in names]
        parts += [_load_description(db_path, t) for t in names]
        _tool("get_schema", t0)
        return "\n\n".join(p for p in parts if p)

    def _final(ctx: str, names: list[str]) -> str:
        if not sample_values:
            return ctx
        t0 = time.perf_counter()
        block = column_value_samples(conn, names)
        _tool("value_samples", t0)
        return f"{ctx}\n\n{block}" if block else ctx

    t0 = time.perf_counter()
    tables = list_tables(conn)
    _tool("list_tables", t0)
    full = _ctx(tables)
    if len(full) <= max_chars or llm is None:
        return _final(full, tables)
    picked = _pick_tables_with_llm(llm, tables, question, tracer, limiter,
                                   on_tool=_tool, sink=sink)
    if picked is None:  # LLM 输出解析失败 → 回退全量（宁可多给不可编造）
        return _final(full, tables)
    return _final(_ctx(picked), picked)


def _pick_tables_with_llm(llm, tables: list[str], question: str, tracer=None,
                          limiter=None, on_tool=None, sink=None) -> list[str] | None:
    # 走 timed_invoke：选表调用也进 tracing（M1 观测盲区修复）
    # M9 票 03：选表 prompt 经 GSSC 唯一出口装配（explore 场景收编，逐字节同旧路；
    # gssc→prompts→precise→executor→本模块成环，惰性 import 同 timed_invoke 先例）
    from qadata.graph.gssc import assemble, fuse_ledger_fields, gather_schema_pick
    from qadata.llm.tracing import timed_invoke

    def _on_compress(info: dict) -> None:
        # 票 04 保险丝入账（explore 无料可砍＝如实入账不硬砍）：账本一行＋直播胶囊
        if tracer is not None:
            tracer.log("budget_fuse", scenario="explore", **fuse_ledger_fields(info))
        if on_tool is not None:
            on_tool("budget_fuse", time.perf_counter())

    t0 = time.perf_counter()
    content = timed_invoke(
        llm, assemble("explore", gather_schema_pick(tables, question),
                      on_compress=_on_compress),
        "explore", tracer, limiter, sink=sink)
    names = [w.strip() for w in str(content).split(",")]
    valid = [n for n in names if n in tables]
    picked = valid if valid else None
    if on_tool is not None:  # 解析失败＝红点（回退照常发生，胶囊如实说）
        on_tool("select_tables", t0, ok=picked is not None)
    return picked
