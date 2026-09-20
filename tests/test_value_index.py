"""M10 票 02 专测：值索引采集（预算封顶·逐值离线 embed，离线零联网零 eval 花费）。

钉票面验收全表：
① 三族纯本地闸——黑名单（ext_id/web_url/open_date 整列不进）/字符预算封顶
  （**整列进出**·first-fit＝一列胖子不饿死全库）/超时·坏视图静默跳列且
  **坏库不连累已采列**（同一次构建里别的列照进档）；
② 逐值 embed 计数入账——批量＝一次调用、sorted 批序确定、同值跨列只 embed 一发、
  重跑零重 embed（值串即缓存键）、**换 embedding 模型整档作废**（表卡同语义一钉）；
③ 档面纪律——缺档 None／坏档如实炸／值无向量＝自相矛盾炸（load_cards 同门）；
④ 超长值不是料（采时即弃不进 dropped 账——那是预算放弃的专账）；窗口外语义如实漏
  （M8 有界窗沿借的可钉面）；
⑤ 查询路径零改动扫描钉——graph/tools/web/eval 源码出现值档符号即红
  （「建了没人读是预期状态」的机制版；examples 零触先例同门）；常量钉。
隔离纪律：全程 root=tmp_path（票 01 评审改①家法，仓库 data/indexes 零染指）。
"""
import inspect
import sqlite3
from pathlib import Path

import pytest

from qadata.retrieval.store import (
    VALUE_FILENAME,
    RetrievalError,
    ValueColumn,
    load_values,
    write_values,
)
from qadata.retrieval.values import (
    VALUE_INDEX_TOTAL_CHARS,
    build_value_index,
    collect_value_candidates,
)
from tests.fakes import FakeEmbedder

_VALS = ("BJ Branch", "Prague - branch", "PODIL", "PUST", "SPL")  # 入档唯一值（memo/ext_id/…不进）


@pytest.fixture
def value_db(tmp_path):
    """闸面全形态小库：可采文本列（branch.nm/ksym.symbol）＋黑名单 TEXT 列
    （ext_id/web_url/open_date/issued——末列＝card.issued 实拍回归钉，只被 "issue"
    键拦下）＋数值列＋超长值列（memo）——表序 branch＜ksym。"""
    p = tmp_path / "val.sqlite"
    conn = sqlite3.connect(p)
    conn.executescript(
        """
        CREATE TABLE branch (id INTEGER PRIMARY KEY, nm TEXT, ext_id TEXT,
                             web_url TEXT, open_date TEXT, issued TEXT, memo TEXT);
        CREATE TABLE ksym (symbol TEXT, amount INTEGER);
        """
    )
    conn.executemany("INSERT INTO branch (nm, ext_id, web_url, open_date, issued, memo) "
                     "VALUES (?, ?, ?, ?, ?, ?)",
                     [("BJ Branch", "E1", "http://a/", "1997-01-01", "1993-12-14", "m" * 70),
                      ("Prague - branch", "E2", "http://b/", "1997-02-02",
                       "1994-01-05", "n" * 70)])
    conn.executemany("INSERT INTO ksym (symbol, amount) VALUES (?, ?)",
                     [("PODIL", 1), ("SPL", 2), ("PUST", 3)])
    conn.commit()
    conn.close()
    return str(p)


@pytest.fixture
def ix(tmp_path):
    return tmp_path / "indexes"


def _emb(vals=_VALS):
    return FakeEmbedder({v: [1.0] for v in vals})


# ── ① 三族闸：黑名单/预算封顶/失败跳列 ──────────────────────────────


def test_gates_blacklist_affinity_long(value_db):
    cols, dropped = collect_value_candidates(value_db)
    assert [(t, c) for t, c, _ in cols] == [("branch", "nm"), ("ksym", "symbol")]
    assert dropped == 0
    assert cols[0][2] == ["BJ Branch", "Prague - branch"]  # ORDER BY 1 档内保序
    assert cols[1][2] == ["PODIL", "PUST", "SPL"]  # 黑名单/数值/超长值列根本不在料里
    # 黑名单在类型闸之后独立生效（ext_id/web_url/open_date 全为 TEXT 声明）：见上＝未入料


def test_budget_whole_column_first_fit(tmp_path, ix):
    """封顶＝整列进出；first-fit＝胖子列（表序在前）出局、后面的窄列照进（不全局停）。"""
    p = tmp_path / "fit.sqlite"
    conn = sqlite3.connect(p)
    conn.executescript("CREATE TABLE t1(a TEXT); CREATE TABLE mid(b TEXT);"
                       "CREATE TABLE z(c TEXT);")
    conn.execute("INSERT INTO t1 VALUES ('x')")
    conn.executemany("INSERT INTO mid VALUES (?)", [("aaaaaaaaaa",) for _ in range(10)])
    conn.execute("INSERT INTO z VALUES ('y')")
    conn.commit()
    conn.close()
    cols, dropped = collect_value_candidates(str(p), total_chars=5)
    assert [(t, c) for t, c, _ in cols] == [("t1", "a"), ("z", "c")]
    assert dropped == 1  # mid（去重后仍 10 字＞余额）整列如实出局，t1/z 照进
    res = build_value_index(str(p), _emb(("x", "y")), root=ix, total_chars=5)
    assert (res.columns, res.values, res.embedded, res.dropped) == (2, 2, 2, 1)
    assert "mid" not in load_values(str(p), root=ix).vecs  # 放弃列半条值都不进档


def test_slow_and_broken_silent_others_survive(value_db, ix):
    """超时（微秒预算打断 80k 行递归 CTE）＋坏视图＝静默跳，已采列零连累——
    且构建照常落档（坏库不连累已采列的档面版）。"""
    conn = sqlite3.connect(value_db)
    conn.execute("CREATE TABLE slow(t TEXT)")
    conn.execute("INSERT INTO slow(t) WITH RECURSIVE cnt(v) AS (SELECT 1 "
                 "UNION ALL SELECT v+1 FROM cnt WHERE v<80000) SELECT 'val'||v FROM cnt")
    conn.execute("CREATE VIEW broken AS SELECT x FROM no_such_table")
    conn.commit()
    conn.close()
    cols, dropped = collect_value_candidates(value_db, budget_s=1e-6)
    assert [(t, c) for t, c, _ in cols] == [("branch", "nm"), ("ksym", "symbol")]
    res = build_value_index(value_db, _emb(), root=ix, budget_s=1e-6)
    assert (res.columns, res.values, res.embedded) == (2, 5, 5)
    assert dropped == 0  # 失败跳列与超预算出局两账分明：slow 是失败不是放弃


def test_scan_window_bounds_values(value_db):
    """有界窗口沿工艺可钉：窗口外的稀有值不进档（如实漏，换 O(window) 成本）。"""
    conn = sqlite3.connect(value_db)
    conn.execute("CREATE TABLE win(v TEXT)")
    conn.executemany("INSERT INTO win(v) VALUES (?)", [(f"w{i}",) for i in range(6)])
    conn.execute("UPDATE win SET v='窗口外稀有值' WHERE rowid=6")
    conn.commit()
    conn.close()
    cols, _ = collect_value_candidates(value_db, scan_window=5)
    win = {(t, c): v for t, c, v in cols}["win", "v"]
    assert win == ["w0", "w1", "w2", "w3", "w4"]  # 第 6 行在窗外


# ── ② embed 计数·零重 embed·换模型作废 ─────────────────────────────


def test_build_counts_rerun_and_stale(value_db, ix):
    emb = _emb()
    res = build_value_index(value_db, emb, root=ix)
    assert (res.columns, res.values, res.embedded, res.reused, res.dropped) == (2, 5, 5, 0, 0)
    assert emb.calls == 1 and emb.batches == [sorted(_VALS)]  # 一批一发·sorted 批序确定
    res2 = build_value_index(value_db, emb, root=ix)
    assert (res2.embedded, res2.reused) == (0, 5) and emb.calls == 1  # 值串即缓存键＝零碰端点
    other = _emb()
    other.model = "other-model"  # 换 embedding 模型＝整档作废（表卡同语义一钉）
    res3 = build_value_index(value_db, other, root=ix)
    assert (res3.embedded, res3.reused) == (5, 0)
    assert load_values(value_db, root=ix).model_id == "other-model"


def test_same_value_shared_vector(tmp_path, ix):
    """同值跨列只 embed 一发（dedupe＝花费纪律），条目各归各列（查询＝逐列 top-K）。"""
    p = tmp_path / "dup.sqlite"
    conn = sqlite3.connect(p)
    conn.executescript("CREATE TABLE a(nm TEXT); CREATE TABLE b(nm TEXT);")
    conn.execute("INSERT INTO a VALUES ('gold'),('silver')")
    conn.execute("INSERT INTO b VALUES ('gold')")
    conn.commit()
    conn.close()
    res = build_value_index(str(p), _emb(("gold", "silver")), root=ix)
    assert (res.values, res.embedded) == (3, 2)
    stored = load_values(str(p), root=ix)
    assert stored.columns == (ValueColumn("a", "nm", ("gold", "silver")),
                              ValueColumn("b", "nm", ("gold",)))
    assert set(stored.vecs) == {"gold", "silver"}  # 档内也去重（合成大库复制表的体积闸）


# ── ③ 档面纪律 ─────────────────────────────────────────────────────


def test_missing_is_none_and_broken_raises(value_db, ix):
    assert load_values(value_db, root=ix) is None  # 缺档＝默认关（本票后＝建了才有人可建）
    build_value_index(value_db, _emb(), root=ix)
    f = next(Path(ix).rglob(VALUE_FILENAME))
    f.write_text("这不是字典", encoding="utf-8")
    with pytest.raises(RetrievalError, match="值索引档"):
        load_values(value_db, root=ix)


def test_self_contradictory_archive_raises(value_db, ix):
    """值列引用了 vecs 里没有的向量＝档自相矛盾，宁炸不带病（坏档如实炸纪律）。"""
    write_values(value_db, "fake-embed", [("t", "c", ["ghost-value"])], {"real": [1.0]},
                 root=ix)
    with pytest.raises(RetrievalError, match="值向量缺失"):
        load_values(value_db, root=ix)


# ── ⑤ 查询路径零改动扫描钉＋常量钉 ────────────────────────────────


def test_query_path_zero_touch():
    """本票只建档面——消费面（图/工具/web/eval）源码出现值档符号即红：
    「建了没人读」是预期状态，接读者＝票 03（examples 零触先例同门）。"""
    import qadata.eval.bird as eval_bird
    import qadata.graph.build as build_mod
    import qadata.graph.gssc as gssc_mod
    import qadata.graph.nodes as nodes_mod
    import qadata.tools.schema as schema_mod
    import qadata.web.app as app_mod
    import qadata.web.feedback as feedback_mod
    import qadata.web.serve as serve_mod
    import qadata.web.sessions as sessions_mod

    for mod in (build_mod, nodes_mod, gssc_mod, schema_mod, app_mod, serve_mod,
                eval_bird, sessions_mod, feedback_mod):  # 覆盖面沿 M9 零触先例全表
        src = inspect.getsource(mod)
        for name in ("load_values", "build_value_index", "value_index", "ValueBuild"):
            assert name not in src, (mod.__name__, name)


def test_constants_pinned():
    assert VALUE_INDEX_TOTAL_CHARS == 20_000  # 判据文件＝.scratch/qadata-m10/ticket02-value-budget.md
