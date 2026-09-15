"""M8 票 02 专测：值采样注入（column_value_samples／build_schema_context 接线）。

钉面＝假库全形态：低基数枚举（'GOLD' vs 'gold' 活病灶）、高基数走**有界窗口＋存储形态
示例**（实施工艺改判①②：窗口防百万行全扫 14s 拖垮 explore；日期分支废除统一形态示例，
A4 误判回归钉在案）、窗口外语义如实漏、单值截断、每表列额度、全局预算确定性裁尾
（双跑同文＝前缀关系）、坏视图/超时静默跳不连累；开关面＝关态零采样查询零文本逐字节
一致（专测钉＋spy）、选表路径只对入选表采、explore 按 Settings.value_sampling 接线
（monkeypatch 捕获 kwargs 直钉）。
"""
import sqlite3

import pytest

import qadata.graph.nodes as nodes_mod
import qadata.tools.schema as schema_mod
from qadata.config import Settings
from qadata.graph.nodes import make_nodes
from qadata.tools.schema import build_schema_context, column_value_samples
from tests.fakes import ScriptedLLM

_TABLES_DDL = """
CREATE TABLE facts(id INTEGER PRIMARY KEY, amount REAL, grade TEXT, memo TEXT,
                   event_date TEXT, code6 TEXT, note19 TEXT);
CREATE TABLE t2(chan TEXT);
INSERT INTO t2(chan) VALUES ('CZE'), ('DEU');
"""


@pytest.fixture
def vconn(tmp_path):
    db = tmp_path / "v.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript(_TABLES_DDL)
    grades = ["bronze", "gold", "GOLD", "silver"]
    for i in range(60):
        conn.execute(
            "INSERT INTO facts(amount, grade, memo, event_date, code6, note19) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (i * 1.5, grades[i % 4], f"m{i}", f"199{3 + i % 6}-0{(i % 9) + 1}-1{i % 9}",
             f"{970000 + i}", "x" * 40),
        )
    conn.commit()
    yield conn
    conn.close()


# ── column_value_samples 假库全形态 ────────────────────────────────


def test_low_card_text_enumerated_with_quotes(vconn):
    out = column_value_samples(vconn, ["facts"])
    line = next(l for l in out.splitlines() if l.startswith("[facts] - grade"))
    assert "'GOLD'" in line and "'gold'" in line  # 大小写变体同框＝病灶根治面
    assert "'bronze'" in line and "'silver'" in line


def test_high_card_gets_shape_examples_numeric_skipped(vconn):
    out = column_value_samples(vconn, ["facts"])
    assert "amount" not in out  # REAL 不碰（类型亲和粗筛）
    memo = next(l for l in out.splitlines() if "memo" in l)
    assert "高基数" in memo and "存储形态示例" in memo
    # 实施工艺改判②的回归钉（真库冒烟实锤 district.A4 误判）：不给「日期形态/
    # 实际范围」这类自称——统一存储形态示例，模型看见书写形态即可，不冒充全量
    assert "库实际范围" not in out and "日期形态" not in out


def test_date_col_shape_visible_via_examples(vconn):
    """票 04 库实际坑（全库日期＝文本 YYYY-MM-DD）：高基数日期列经形态示例
    照样把 ISO 书写形态递到模型眼前——不再依赖专门日期分支。"""
    out = column_value_samples(vconn, ["facts"])
    line = next(l for l in out.splitlines() if "event_date" in l)
    assert "'199" in line  # ISO 形态示例可见


def test_scan_window_bounds_big_table(vconn):
    """窗口语义可钉：>scan_window 行的表，枚举值只来自前 window 行——
    窗口末尾之后的稀有值不进样本（如实漏，换 O(window) 成本与双跑确定性）。"""
    from qadata.tools.schema import _VALUE_SCAN_WINDOW
    vconn.execute("CREATE TABLE big(v TEXT)")
    n = _VALUE_SCAN_WINDOW
    vconn.executemany("INSERT INTO big(v) VALUES (?)",
                      [(f"x{i}",) for i in range(n + 1)])  # 第 n+1 行在窗口外
    vconn.execute(f"UPDATE big SET v='窗口外的稀有值' WHERE rowid={n + 1}")
    out = column_value_samples(vconn, ["big"])
    assert "高基数" in out and "窗口外的稀有值" not in out


def test_cell_chars_truncated(vconn):
    out = column_value_samples(vconn, ["facts"])
    assert "x" * 30 + "…" in out and "x" * 31 not in out


def test_columns_per_table_cap_is_deterministic(vconn):
    vals = ",".join(f"'{c}v'" for c in "abcdefghij")
    vconn.executescript(
        "CREATE TABLE wide(a TEXT,b TEXT,c TEXT,d TEXT,e TEXT,f TEXT,g TEXT,"
        f"h TEXT,i TEXT,j TEXT); INSERT INTO wide VALUES ({vals});")
    out = column_value_samples(vconn, ["wide"])
    assert out.count("[wide] - ") == 8  # 表序×列序前 8 列，整列进出


def test_global_budget_prefix_and_double_run_identical(vconn):
    # 全局裁尾＝整列进出、前缀关系（150 夹在「首行进得来、全量进不完」之间）
    full = column_value_samples(vconn, ["t2", "facts"])
    cut = column_value_samples(vconn, ["t2", "facts"], total_chars=150)
    assert 0 < len(cut) < len(full) and full.startswith(cut)
    assert full == column_value_samples(vconn, ["t2", "facts"])  # 双跑逐字节


def test_column_budget_trims_trailing_columns(vconn):
    # 表内列预算：低基数枚举在前、高基数日期列在后（撞 cols_per_table 前段全进）
    full = column_value_samples(vconn, ["facts"])
    cut = column_value_samples(vconn, ["facts"], cols_per_table=2)
    assert cut and cut in full and full.count("[facts] - ") > cut.count("[facts] - ") == 2
    assert cut == column_value_samples(vconn, ["facts"], cols_per_table=2)


def test_broken_view_and_timeout_silent_skip(vconn, tmp_path):
    vconn.execute("CREATE VIEW broken AS SELECT x FROM no_such_table")
    assert column_value_samples(vconn, ["broken"]) == ""  # 静默，不抛
    # 8 万行大列在微秒预算下必被中断；同一次调用里后表照采（不连累本体）
    vconn.execute("CREATE TABLE slow(t TEXT)")
    vconn.execute("INSERT INTO slow(t) WITH RECURSIVE cnt(v) AS (SELECT 1 "
                  "UNION ALL SELECT v+1 FROM cnt WHERE v<80000) SELECT 'val'||v FROM cnt")
    out = column_value_samples(vconn, ["slow", "t2"], budget_s=1e-6)
    assert "slow" not in out and "[t2] - chan" in out


# ── build_schema_context 开关面 ────────────────────────────────────


def _calls_spy(monkeypatch):
    calls = []
    real = schema_mod.column_value_samples

    def spy(conn, tables, **kw):
        calls.append(list(tables))
        return real(conn, tables, **kw)

    monkeypatch.setattr(schema_mod, "column_value_samples", spy)
    return calls


def test_off_state_zero_query_byte_identical(vconn, monkeypatch):
    calls = _calls_spy(monkeypatch)
    base = build_schema_context(vconn, "问题")
    with_flag = build_schema_context(vconn, "问题", sample_values=False)
    assert base == with_flag and "取值样本" not in base
    assert calls == []  # 关态连一条 DISTINCT 都不发（不是发了不贴）


def test_on_appends_sample_block(vconn):
    base = build_schema_context(vconn, "问题")
    on = build_schema_context(vconn, "问题", sample_values=True)
    assert on.startswith(base) and "\n\n列取值样本" in on and "[facts] - grade" in on


def test_on_no_sampleable_tables_identical(vconn, tmp_path):
    # 独立小库：只有数值列，开关开着也无一可采——输出必须与关态一样干净
    db = tmp_path / "nums.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE nums(x INTEGER PRIMARY KEY)")
    conn.commit()
    out = build_schema_context(conn, "q", sample_values=True)
    assert "取值样本" not in out  # 空块不贴尾缀分隔符
    conn.close()


def test_picked_subset_only_gets_sampled(vconn, monkeypatch):
    """采样跟最终入选表走（在 _final 后算），回退全量时也对全量采——
    max_chars 压 1 逼出 LLM 选表路径。"""
    llm = ScriptedLLM(["t2"])
    out = build_schema_context(vconn, "渠道有哪些", llm=llm, max_chars=1,
                               sample_values=True)
    assert "[t2] - chan" in out and "[facts]" not in out
    assert llm.calls == 1  # 选表调用照旧一次，采样零新增调用


# ── explore 接线 ───────────────────────────────────────────────────


def _wired_kwargs(monkeypatch, fixture_db, value_sampling: bool):
    seen = {}

    def fake_ctx(conn, question, **kw):
        seen.update(kw)
        return "CTX"

    monkeypatch.setattr(nodes_mod, "build_schema_context", fake_ctx)
    nodes = make_nodes(ScriptedLLM([]), settings=Settings(
        api_key="", base_url="", model="", value_sampling=value_sampling))
    out = nodes["explore"]({"db_path": fixture_db, "question": "q"})
    assert out == {"db_schema": "CTX", "matched_metric": None}
    return seen["sample_values"]


def test_explore_passes_flag_on_and_off(monkeypatch, fixture_db):
    assert _wired_kwargs(monkeypatch, fixture_db, True) is True
    assert _wired_kwargs(monkeypatch, fixture_db, False) is False
