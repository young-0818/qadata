"""Schema 探索工具：列出表、取 DDL、采样数据、构建上下文。"""
import csv
import sqlite3
import time
from pathlib import Path

FULL_SCHEMA_LIMIT = 8000  # schema 全量超过该字符数才请求 LLM 选表

# ── M8 票 02 值采样：预算常数（放宽＝改票面）。M10 票 02 起部分公开＝值索引沿借
# 采集工艺（评审家法「私有跨包 import 不留」，load_description 先例同门）──
_VALUE_COLS_PER_TABLE = 8      # 每表最多采样的文本列数
_VALUE_PER_COL = 10            # 逐列枚举值上限（取满 +1 判高基数）
_VALUE_CELL_CHARS = 30         # 单值最多字符（超出截断）
_VALUE_TOTAL_CHARS = 2000      # 全局样本块字符预算（确定性裁尾＝表序×PRAGMA 列序）
VALUE_SCAN_WINDOW = 2000       # DISTINCT 前有界子查询（实施工艺改判①）：裸 DISTINCT
                               # 在百万行表全扫（真库冒烟 14s/库不可接受，explore 被拖垮）。
                               # 2000＝看见存储形态绰绰有余（ISO 日期/大小写/常见枚举全现身，
                               # 大库实测 50k 窗仍 6.8s、2k 窗进毫秒带）；成本 O(window) 且
                               # **确定**——同库双跑同文评测可复现，截断点不由 wall-clock 决定。
                               # 窗口外罕见枚举值可能漏，但采样目的＝形态与非全量清单，如实漏。
VALUE_QUERY_TIMEOUT_S = 2.0    # 单查询兜底超时（视图背后巨型 join 时仍拦得住；中断＝静默跳列）
# 样本块节头（M9 票 04：保险丝砍值采样按此定位整块边界——单源字面，gssc 惰性 import 共读）
VALUE_SAMPLE_HEADER = "列取值样本（库实际存储形态，WHERE 字面量以此为准）："
# 采样候选声明类型（sqlite 亲和性粗筛；BLOB/数值列枚举值对 WHERE 字面量病灶无益）
SAMPLE_TYPE_KEYS = ("CHAR", "CLOB", "TEXT", "DATE", "TIME")


def is_text_affinity(declared_type: str) -> bool:
    """声明类型是否文本亲和（M10 票 02 起公开＝值索引沿借同闸，闸门唯一＝SAMPLE_TYPE_KEYS）。"""
    return any(k in declared_type.upper() for k in SAMPLE_TYPE_KEYS)


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


def load_description(db_path: str | None, table: str) -> str:
    """读 database_description/{table}.csv（BIRD 官方列注释，表头自适应）；
    无文件/读失败静默返回空串（锦上添花不连累本体）。M10 票 01 起公开＝表卡
    描述料的共读面（retrieval/cards 同门复用，私有跨包 import 不留）。"""
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


def distinct_values(conn: sqlite3.Connection, table: str, col: str, *, max_rows: int,
                    scan_window: int = VALUE_SCAN_WINDOW,
                    budget_s: float = VALUE_QUERY_TIMEOUT_S) -> list:
    """有界窗口 DISTINCT 取值（M8 票 02 实施工艺改判①的公开复用面，M10 票 02 值索引沿借）：
    先 LIMIT scan_window 再 DISTINCT——百万行表裸 DISTINCT 全扫（真库冒烟 14s/库）不可接受；
    窗口把每列成本压到 O(window) 且确定（同库双跑同序，截断点不由 wall-clock 决定）。
    失败/超时抛 sqlite3.Error——**吞不吞由调用方纪律定**（采样面静默跳列、值面整列出局）。
    表/列名源自 PRAGMA 非题面（sample_rows 同信任域）。"""
    rows = _query_with_budget(
        conn, f'SELECT DISTINCT "{col}" FROM '
              f'(SELECT "{col}" FROM "{table}" WHERE "{col}" IS NOT NULL '
              f'LIMIT {int(scan_window)}) ORDER BY 1 LIMIT {int(max_rows)}', budget_s)
    return [r[0] for r in rows]


def _column_sample_line(conn: sqlite3.Connection, table: str, col: str, *,
                        per_col: int, budget_s: float,
                        scan_window: int = VALUE_SCAN_WINDOW) -> str:
    """一列一行样本。**有界窗口 DISTINCT**（先 LIMIT scan_window 再 DISTINCT——
    百万行大表裸 DISTINCT 全扫实测 14s/库拖垮 explore，不可接受；窗口把每列成本压到
    O(window) 且确定，同库双跑同文＝评测可复现）。取满 per_col+1 行判高基数。

    高基数列**不再分日期/非日期**（实施工艺改判②）：真库冒烟实锤 district.A4 区名混内容
    列被日期正则误判注出无意义范围——统一注「存储形态示例（前 N 值，窗口内）」，让模型
    看见实际书写形态（ISO 日期、大小写、带不带空格）即可，不冒充全量。低基数直接全枚举。
    表/列名源自 PRAGMA 非题面，直查只读 conn 有 sample_rows 先例同信任域。"""
    try:
        vals = distinct_values(conn, table, col, max_rows=per_col + 1,
                               scan_window=scan_window, budget_s=budget_s)
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
    budget_s: float = VALUE_QUERY_TIMEOUT_S,
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
            if not is_text_affinity(decl):
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


def foreign_key_closure(conn: sqlite3.Connection, tables: list[str]) -> list[str]:
    """宽进窄出的最后一段补漏（M10 票 01，DataAgent 外键补漏同款）：选中表的
    一跳外键亲戚（出站父表＋入站子表）确定性补上——JOIN 需要的桥表不靠向量与
    LLM 猜。单边一跳、不做传递闭包（补两跳＝把半个库拖进 prompt）。
    表/列名源自 sqlite_master 非题面（sample_rows 同信任域）；PRAGMA 炸＝静默跳
    该表边（锦上添花不连累本体），出序＝list_tables 字典序（确定性）。"""
    all_tables = list_tables(conn)
    edges: set[tuple[str, str]] = set()  # (子表, 父表)
    for t in all_tables:
        try:
            rows = conn.execute(f'PRAGMA foreign_key_list("{t}")').fetchall()
        except sqlite3.Error:
            continue
        edges.update((t, str(r[2])) for r in rows if r[2])
    picked = set(tables)
    rel = picked | {p for c, p in edges if c in picked} | {c for c, p in edges if p in picked}
    return [t for t in all_tables if t in rel]


def _sticker_detail(block: str) -> str:
    """把值纸条块压成一行命中明细供控制台胶囊显示（如 `district.A3→'east Bohemia'、card.type→'gold'`）。
    格式单源＝retrieval.values.format_value_sticker（逐行 `- 表.列 —— 库里实际这么存：值｜值`）——
    文案若改此处随动，有 test_sticker_detail_tracks_sticker_format 钉死防漂移。截 ≤160 字防灌 DOM／撑会话档 trail。"""
    segs = []
    for ln in block.splitlines():
        if not ln.startswith("- "):
            continue
        col, _, rest = ln[2:].partition(" —— ")
        val = rest.split("库里实际这么存：", 1)[-1] if "库里实际这么存：" in rest else rest
        segs.append(f"{col}→{val.replace('｜', '、')}")
    out = "；".join(segs)
    return out[:157] + "…" if len(out) > 160 else out


def build_schema_context(
    conn: sqlite3.Connection, question: str, llm=None, max_chars: int = FULL_SCHEMA_LIMIT,
    tracer=None, db_path: str | None = None, limiter=None, sample_values: bool = False,
    on_event=None, sink=None, table_recall=None, value_link=None,
) -> str:
    """构建给 LLM 的 schema 上下文：小库全量；大库让 LLM 先选相关表。
    db_path 提供时，附带选中表的 database_description 列注释（M3 #10）。
    sample_values＝票 02 值采样开关（对最终入选表追加样本块；False＝零查询
    零文本与现状逐字节一致——专测钉，关态不探一条 DISTINCT）。
    on_event/sink（M8 票 06 帧喂厚）＝explore 子步骤发 kind:"tool" 帧
    （list_tables/get_schema/select_tables/value_samples），选表 LLM 调用的 token
    经 sink 流进该步结果帧；缺省 None 零发、与现状逐行为一致。
    table_recall（M10 票 01）＝表卡粗召回调（question→候选表名 list，None＝缺位/
    降级，账本在闭包内自持）——**只在大库分支消费**：小库全量路连调用都不发生
    （索引文件根本不读，逐字节现状姊妹钉）；大库＝宽进（粗召 top-K）→窄出
    （既有 LLM 精选，prompt 骨架零改动）→外键补漏，任何检索缺位＝走现状一把梭。
    value_link（M10 票 03）＝值纸条调（入选表→纸条块字符串，""＝不贴）——与表卡
    相反，**小库大库都触发**（值域病灶全在小库考面，spec §一）；块贴 schema 上下文
    最末（值采样块之后＝保险丝 split 切尾不连累前料），账本与降级在闭包内自持
    （retrieval/values.build_value_link），本层零知情；缺位 None＝逐字节现状。"""
    from qadata.llm.tracing import tool_frame  # 帧形单源（timed_invoke 同款惰性路）

    def _tool(name: str, t0: float, ok: bool = True, detail: str | None = None) -> None:
        if on_event is not None:
            on_event(tool_frame("explore", name, t0, ok, detail))

    def _ctx(names: list[str]) -> str:
        t0 = time.perf_counter()
        parts = [get_schema(conn, t) for t in names]
        parts += [load_description(db_path, t) for t in names]
        _tool("get_schema", t0)
        return "\n\n".join(p for p in parts if p)

    def _final(ctx: str, names: list[str]) -> str:
        if sample_values:
            t0 = time.perf_counter()
            block = column_value_samples(conn, names)
            _tool("value_samples", t0)
            if block:
                ctx = f"{ctx}\n\n{block}"
        if value_link is not None:
            t0 = time.perf_counter()
            block = value_link(names)  # 纸条块贴最末（淘汰序「撤值纸条」在「砍值采样」前，切尾各不连累）
            # 控制台「实际取值」胶囊：绿＝真贴上了值、灰＝跑过但没命中（缺索引/无关键词，降级真值在账本 value_link 行）；
            # detail 把命中明细摊开（列→库内实际存储值），让胶囊说清"揪出了什么"而非只剩一个耗时。
            _tool("value_link", t0, ok=bool(block), detail=_sticker_detail(block) if block else None)
            if block:
                ctx = f"{ctx}\n\n{block}"
        return ctx

    t0 = time.perf_counter()
    tables = list_tables(conn)
    _tool("list_tables", t0)
    full = _ctx(tables)
    if len(full) <= max_chars or llm is None:
        return _final(full, tables)
    if table_recall is not None and (cands := table_recall(question)):
        # 大库宽进窄出（M10 票 01）：粗召 top-K 防漏表 → 精选防错用 → 外键补漏。
        # 窄出解析失败＝回全量现状（与旧路同形：宁可多给不可编造，检索不添新的失败形态）
        picked = _pick_tables_with_llm(llm, cands, question, tracer, limiter,
                                       on_tool=_tool, sink=sink)
        if picked is None:
            return _final(full, tables)
        wide = foreign_key_closure(conn, picked)
        return _final(_ctx(wide), wide)
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
