"""M10 票 01 专测：检索骨架＋索引跟库归家＋表卡路（宽进窄出·现读降级，离线零联网）。

钉票面验收全表：
① index-build 纯离线：逐卡 embed 计数如实（批量＝一次调用）、**重跑条目哈希未变＝
  零重 embed**（manifest 钉）、换模型整档作废；
② 合成大库（FakeEmbedder＋ScriptedLLM，零 API）：gold 表稳定经粗召 top-K→LLM 精选
  →外键补漏捞回（e2e 走 run_question 全挂接面）；粗召炸（端点挂/过期/维度不合）＝
  降级现状＋账本入账钉；窄出解析失败＝回全量逐字节现状＋select_tables 红胶囊入账钉
  （M8 票 06 既有账形）——一切缺位永不拦答题；
③ 小库逐字节现状姊妹钉——全量路**根本不读索引文件**（召回调不被调用）；
  serve 缺档/坏档/过期三态各一钉（index_announcements 纯函数）；
④ 挂接面末位参纪律钉（build_graph/resume_question 末位＋不进状态键）；账本
  table_recall 行无 token 标记＝不烧生成调用数；COARSE_TOP_K 常量钉。
隔离纪律：检索档面全程 root=tmp_path——不碰仓库真实 data/indexes（conftest
dotenv 隔离同款家法，评审追补）。零 eval 花费：全程假模型假向量器。例题面的
"eval 零触"钉在 test_embedding_recall（M10 两义分家：库派生物验收必须经 eval，
人签知识照旧零触；CLI ask 不挂粗召回＝现状，裁决注记回写票 01）。
"""
import inspect
import json
import sqlite3
from pathlib import Path

import pytest

from qadata.config import Settings
from qadata.graph.build import build_graph, resume_question, run_question
from qadata.graph.state import AgentState
from qadata.llm.tracing import TraceLogger
from qadata.retrieval.cards import (
    COARSE_TOP_K,
    build_index,
    build_table_recall,
    collect_card_texts,
)
from qadata.retrieval.store import (
    CARDS_FILENAME,
    RetrievalError,
    content_hash,
    index_dir_for,
    index_status,
    load_cards,
)
from qadata.tools.db import open_readonly
from qadata.tools.schema import (
    FULL_SCHEMA_LIMIT,
    build_schema_context,
    foreign_key_closure,
)
from qadata.web.agents import AgentStore
from qadata.web.serve import index_announcements
from tests.fakes import BoomEmbedder, FakeEmbedder, ScriptedLLM

_S = Settings(api_key="", base_url="", model="test-model")


@pytest.fixture
def big_db(tmp_path):
    """合成大库：branch（父 district、子 account）＋20 张宽噪声表撑过 schema 阈值。"""
    p = tmp_path / "big.sqlite"
    conn = sqlite3.connect(p)
    conn.executescript(
        """
        CREATE TABLE district (id INTEGER PRIMARY KEY, nm TEXT);
        CREATE TABLE branch (id INTEGER PRIMARY KEY, nm TEXT,
                             district_id INTEGER REFERENCES district(id));
        CREATE TABLE account (id INTEGER PRIMARY KEY, branch_id INTEGER REFERENCES branch(id),
                              amount REAL);
        INSERT INTO district VALUES (1, 'Prague');
        INSERT INTO branch VALUES (1, 'BJ Branch', 1);
        INSERT INTO account VALUES (1, 1, 100.0);
        """
    )
    for i in range(20):
        cols = ", ".join(f"noise_{i:02d}_attribute_{j:02d} TEXT" for j in range(30))
        conn.execute(f"CREATE TABLE noise_{i:02d} (id INTEGER PRIMARY KEY, {cols})")
    conn.commit()
    conn.close()
    return str(p)


@pytest.fixture
def ix(tmp_path):
    """检索档根目录（隔离钉：全部读写落 tmp_path，仓库 data/indexes 零染指）。"""
    return tmp_path / "indexes"


def _script(db, question):
    """每卡一行独热向量（构建序＝库序＝同分决定性）；题面＝branch 那一位。"""
    entries = collect_card_texts(db)
    dim = len(entries)
    hit = [i for i, (t, _) in enumerate(entries) if t == "branch"] or [0]
    vecs = {text: [1.0 if k == i else 0.0 for k in range(dim)]
            for i, (_, text) in enumerate(entries)}
    vecs.setdefault(question, [1.0 if k == hit[0] else 0.0 for k in range(dim)])
    return vecs


def _emb(db, question):
    return FakeEmbedder(_script(db, question))


def _rows(tracer_path):
    return [json.loads(ln) for ln in
            Path(tracer_path).read_text(encoding="utf-8").splitlines()]


# ── ① 构建：manifest、零重 embed、换模型作废 ─────────────────────────


def test_build_then_rerun_zero_reembed(big_db, ix):
    emb = _emb(big_db, "branch 相关")
    res = build_index(big_db, emb, root=ix)
    assert (res.tables, res.embedded, res.reused) == (23, 23, 0)
    assert emb.calls == 1  # 逐卡批量＝一次向量化调用（构建期唯一花钱处）
    stored = load_cards(big_db, root=ix)
    assert stored.model_id == "fake-embed" and stored.content_hash == content_hash(big_db)
    res2 = build_index(big_db, emb, root=ix)
    assert (res2.embedded, res2.reused) == (0, 23)  # 条目哈希未变＝零重 embed
    assert emb.calls == 1  # 第二次构建根本没碰端点
    other = FakeEmbedder({t: [1.0] for _, t in collect_card_texts(big_db)})
    other.model = "other-model"  # FakeEmbedder.model 是类默认值，实例遮蔽＝换模型注入
    res3 = build_index(big_db, other, root=ix)  # 换模型＝旧向量整档作废（全量重 embed）
    assert (res3.embedded, res3.reused) == (23, 0)
    assert load_cards(big_db, root=ix).model_id == "other-model"


def test_broken_index_load_raises(big_db, ix):
    build_index(big_db, _emb(big_db, "x"), root=ix)
    f = index_dir_for(big_db, root=ix) / CARDS_FILENAME
    f.write_text("这不是字典", encoding="utf-8")
    with pytest.raises(RetrievalError, match="表卡档"):
        load_cards(big_db, root=ix)


# ── ② 宽进窄出 e2e：粗召 top-K→精选→外键补漏（run_question 全挂接面）───


def test_coarse_narrow_fk_e2e(big_db, tmp_path, ix):
    tracer = TraceLogger(tmp_path / "traces.jsonl", run_id="r1")
    emb = _emb(big_db, "改写")
    build_index(big_db, emb, root=ix)
    recall = build_table_recall(big_db, emb, tracer=tracer, root=ix)
    llm = ScriptedLLM(["改写", "branch", "SELECT nm FROM branch", "BJ Branch"])
    ans = run_question(big_db, "branch 名字", llm=llm, settings=_S, tracer=tracer,
                       table_recall=recall)
    assert ans.failed is False
    assert llm.calls == 4  # understand/选表/generate/respond——检索零新增生成调用
    assert emb.calls == 2  # 建库 1 ＋ 问 1
    pick_prompt = llm.prompts[1]
    assert "branch" in pick_prompt and "noise_06" in pick_prompt  # 粗召进精选料
    assert "noise_07" not in pick_prompt  # top-10 之外的噪声表根本不进精选 prompt（宽进闸生效）
    gen = llm.prompts[2]
    # 窄出精选＝branch，外键补漏整表带上父 district 与子 account；未选噪声不进
    assert "CREATE TABLE branch" in gen and "CREATE TABLE district" in gen \
        and "CREATE TABLE account" in gen
    for noise in ("noise_00", "noise_07"):
        assert f"CREATE TABLE {noise}" not in gen
    rows = [x for x in _rows(tmp_path / "traces.jsonl") if x["node"] == "table_recall"]
    assert len(rows) == 1 and rows[0]["outcome"] == "ok" and rows[0]["cands"] == COARSE_TOP_K
    assert "input_tokens" not in rows[0]  # 不烧生成调用数（recall 行同形）
    assert tracer.usage_for("-")["llm_calls"] == 4  # 4 次真调用，检索行不掺账


def test_recall_memo_one_vectorize_per_question(big_db, ix):
    """同问重装配（指标降级环重进 explore＝调用方重取）＝memo 命中返回同对象，
    向量化不逐次翻倍（recall 先例账形）。"""
    emb = _emb(big_db, "Q")
    build_index(big_db, emb, root=ix)
    assert emb.calls == 1  # 建库＝批量一发
    recall = build_table_recall(big_db, emb, root=ix)
    first = recall("Q")
    assert first is not None and recall("Q") is first
    assert emb.calls == 2  # 问面只 1 发（第二次＝memo，不翻倍）


def test_embed_boom_degrades_status_quo(big_db, ix):
    """端点挂＝粗召缺位→现状一把梭（精选吃**全量**表清单、无外键补漏——旧路原样）
    ＋入账不静默，schema 照常出。"""
    trace_path = Path(big_db).parent / "t.jsonl"
    tracer = TraceLogger(trace_path, run_id="r")
    build_emb = _emb(big_db, "Q")
    build_emb.model = "boom-embed"  # 建档模型名对齐＝测的是端点挂那一路（非过期）
    build_index(big_db, build_emb, root=ix)
    recall = build_table_recall(big_db, BoomEmbedder(), tracer=tracer, root=ix)
    llm = ScriptedLLM(["branch"])
    conn = open_readonly(big_db)
    try:
        out = build_schema_context(conn, "Q", llm=llm, max_chars=100,
                                   table_recall=recall)
    finally:
        conn.close()
    assert "noise_19" in llm.prompts[0]  # 现状一把梭：全量表清单进精选 prompt
    assert "CREATE TABLE branch" in out  # 窄出照旧
    assert "CREATE TABLE district" not in out  # 缺位形态＝旧路语义，补漏不越位
    rows = [x for x in _rows(trace_path) if x["node"] == "table_recall"]
    assert len(rows) == 1 and rows[0]["outcome"] == "failed"
    assert "向量化调用失败" in rows[0]["reason"] and "input_tokens" not in rows[0]


def test_stale_index_runtime_degrade(big_db, ix):
    """过期（建档 model_id ≠ 当前 embedder）在查询面＝同纪律降级＋入账（serve 播报
    之外运行时也拦——换模型后旧向量宁作废不误打分）。"""
    build_index(big_db, _emb(big_db, "Q"), root=ix)
    other = FakeEmbedder(_script(big_db, "Q"))
    other.model = "other-model"
    trace_path = Path(big_db).parent / "t2.jsonl"
    recall = build_table_recall(big_db, other, tracer=TraceLogger(trace_path, run_id="r"),
                                root=ix)
    assert recall("Q") is None
    row = [x for x in _rows(trace_path) if x["node"] == "table_recall"][-1]
    assert row["outcome"] == "failed" and "过期" in row["reason"]
    assert recall("Q") is None and len(
        [x for x in _rows(trace_path) if x["node"] == "table_recall"]) == 1  # memo 也吃降级


def test_dim_mismatch_degrades_and_parse_fail_pins(big_db, ix):
    """两形合一钉：①维度不合（换模型残留/端点异常）＝宁降级不误打分（不外抛、
    不拦答题）；②粗召正常但窄出解析失败＝回全量现状逐字节＋入账——旧路账形＝
    select_tables 红胶囊（M8 票 06，本路零扩契约不另开 ledger）。"""
    emb = FakeEmbedder({"Q": [0.5]})  # 1 维 vs 档内 23 维
    build_index(big_db, _emb(big_db, "Q"), root=ix)
    conn = open_readonly(big_db)
    try:
        assert build_table_recall(big_db, emb, root=ix)(  # 维度不合→None（降级），不外抛
            "Q") is None
        good = build_table_recall(big_db, _emb(big_db, "Q"), root=ix)
        frames = []
        boom_llm = ScriptedLLM(["这张表我不要输出"])  # 解析不出合法表名
        out_fail = build_schema_context(conn, "Q", llm=boom_llm, max_chars=100,
                                        table_recall=good, on_event=frames.append)
        plain_llm = ScriptedLLM(["这张表我不要输出"])
        out_plain = build_schema_context(conn, "Q", llm=plain_llm, max_chars=100)
        assert out_fail == out_plain  # 逐字节现状（回全量）
        assert any(f.get("kind") == "tool" and f["tool"] == "select_tables"
                   and f["ok"] is False for f in frames)  # 降级入账＝红胶囊（票 04 双出口形制）
    finally:
        conn.close()


# ── ③ 小库逐字节现状（全量路根本不读索引文件）＋ serve 三态播报 ────────


def test_small_db_never_touches_index(fixture_db, ix):
    """索引已建、召回调已挂：小库（≤FULL_SCHEMA_LIMIT）连调用都不发生——
    schema 上下文与无检索时逐字节一致（家法：小库考卷不是约束，但现状不破）。"""
    build_index(fixture_db, _emb(fixture_db, "Q"), root=ix)
    touched = []
    recall = build_table_recall(fixture_db, _emb(fixture_db, "Q"), root=ix)

    def boom(q):
        touched.append(q)
        return recall(q)

    conn = open_readonly(fixture_db)
    try:
        with_recall = build_schema_context(conn, "Q", table_recall=boom)
        plain = build_schema_context(conn, "Q")
    finally:
        conn.close()
    assert not touched and with_recall == plain


def test_fk_closure_is_one_hop_deterministic(big_db):
    conn = open_readonly(big_db)
    try:
        assert foreign_key_closure(conn, ["branch"]) == ["account", "branch", "district"]
        # 单边一跳：从 district 出发只捞到子 branch，不带 branch 的子 account
        assert foreign_key_closure(conn, ["district"]) == ["branch", "district"]
        assert foreign_key_closure(conn, ["noise_03"]) == ["noise_03"]
    finally:
        conn.close()


def test_serve_announcements_missing_broken_stale(tmp_path, fixture_db, ix):
    store = AgentStore(tmp_path / "agents", metrics_dir=tmp_path / "metrics")
    a = store.create("学校")
    p = store.store_datasource(a.id, Path(fixture_db).read_bytes(), "school.sqlite")
    assert "缺档" in "\n".join(index_announcements(store, "fake-embed", root=ix))
    d = index_dir_for(p, root=ix)
    d.mkdir(parents=True, exist_ok=True)
    (d / CARDS_FILENAME).write_text("这不是字典", encoding="utf-8")
    assert "坏档" in "\n".join(index_announcements(store, "fake-embed", root=ix))
    build_index(p, _emb(p, "Q"), root=ix)
    lines = index_announcements(store, "fake-embed", root=ix)
    assert any("表卡索引开" in ln for ln in lines) and not any("缺档" in ln for ln in lines)
    assert "过期" in "\n".join(index_announcements(store, "other-model", root=ix))


def test_index_follows_db_not_agent(tmp_path, fixture_db, ix):
    """库派生索引跟库走（ADR-0004）：同一库换路径＝换指纹＝天然作废；
    智能体目录里没有、也不需要这份档。"""
    build_index(fixture_db, _emb(fixture_db, "Q"), root=ix)
    assert index_status(fixture_db, "fake-embed", root=ix) == "ok"
    copy = Path(tmp_path) / "elsewhere" / "school.sqlite"
    copy.parent.mkdir(parents=True)
    copy.write_bytes(Path(fixture_db).read_bytes())  # 同字节换路径
    assert index_status(copy, "fake-embed", root=ix) == "missing"
    assert index_dir_for(fixture_db, root=ix) != index_dir_for(copy, root=ix)
    assert index_dir_for(copy, root=ix) == index_dir_for(str(copy.resolve()), root=ix)


# ── ④ 纪律与常量钉 ──────────────────────────────────────────────────


def test_wiring_tail_params_and_no_state_key():
    for fn in (build_graph, resume_question):
        params = list(inspect.signature(fn).parameters)
        assert params[-2:] == ["recall", "table_recall"], fn.__name__
    assert "table_recall" not in AgentState.__annotations__  # 不进状态键（可调用沿参）
    assert COARSE_TOP_K == 10
    assert FULL_SCHEMA_LIMIT == 8000  # 大库闸阈值＝表卡路的入口条件（现状家法同源）
