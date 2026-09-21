"""M10 票 05 专测：知识召回路＋口径优先级接线（假 embedder 离线，零 eval 花费零联网）。

钉票面验收全表：
① 端到端：相关口径进 generate prompt、无关不进；空字典/缺 embedder/召回炸三路
  降级＋缺档＝逐字节现状姊妹钉（prompt 与无挂接基线等值、llm.calls 不掺账）；
② 落位钉＝字典块在 schema 后、参考例题前（例题「贴用户问题最近」旧位不动），
  并钉 evidence 退役键＝请求残键/状态残键零注入（ADR-0008 字典单通道，
  双跑金标准侧另有防回吹钉）；
③ 保险丝「撤字典块」位在 test_budget_fuse 全链扩钉（撤纸条同族、撤例题之前）；
④ 账本 knowledge_recall 行形制（hits/pool/model/latency，无 token 标记＝不烧
  生成调用数）＋题面 memo 重试环不翻倍＋过期显式闸（票 04 移交在册：字典路无
  关键词保底，同维跨模型向量不自提示）＋挂账档读侧显形；
⑤ 条数/阈值定标值数值钉＋「票 07 定标/已定标」注记钉（票 07 后语义＝定标注记在册）
  ＋节头单源钉；select 第四格
  恒等钉；挂接面＝eval 通道在册（两义分家钉生效）、CLI ask 不挂（裁决同族）。
隔离纪律：全程 tmp_path（真实 data/agents 零染指）。进料侧钉在 test_knowledge_intake。
"""
import inspect
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import qadata.retrieval.knowledge as knowledge_mod
from qadata.config import Settings
from qadata.graph import gssc
from qadata.graph.build import run_question
from qadata.graph.prompts import EXAMPLES_HEADER, format_examples_block
from qadata.graph.state import AgentState
from qadata.llm.tracing import TraceLogger
from qadata.retrieval.knowledge import (
    KNOWLEDGE_FILENAME,
    KNOWLEDGE_HEADER,
    KNOWLEDGE_MIN_SCORE,
    KNOWLEDGE_TOP_K,
    build_knowledge_recall,
    feed_knowledge,
    format_knowledge_block,
)
from qadata.web.agents import AgentStore
from qadata.web.app import create_app
from tests.fakes import BoomEmbedder, FakeEmbedder, ScriptedLLM
from tests.test_web_api import _HAPPY_SCRIPT, _agent_with_datasource

_S = Settings(api_key="", base_url="", model="test-model")

REL = "正常贷款：状态 'A' 表示正常。"      # 相关条目（与题面同向＝进块）
IRR = "汇率口径：1 美元兑 7 元。"          # 无关条目（正交＝阈值下出局）
Q1 = "有多少正常贷款"
VECS = {REL: [1.0, 0.0], IRR: [0.0, 1.0], Q1: [1.0, 0.0]}
_GOOD_SQL = "SELECT name FROM students WHERE id = 2"


@pytest.fixture
def store(tmp_path):
    return AgentStore(tmp_path / "agents")


@pytest.fixture
def kb_dir(tmp_path):
    """进料一个两条口径的字典档（走唯一写入口 feed_knowledge＝自家路自证）。"""
    d = tmp_path / "kb"
    d.mkdir()
    src = tmp_path / "dict.md"
    src.write_text(f"{REL}\n\n{IRR}\n", encoding="utf-8")
    feed_knowledge(d, src, FakeEmbedder(VECS))
    return d


def _rows(tracer_path: Path) -> list[dict]:
    return [json.loads(ln) for ln in
            tracer_path.read_text(encoding="utf-8").splitlines()]


def _kb_rows(tracer_path: Path) -> list[dict]:
    return [x for x in _rows(tracer_path) if x["node"] == "knowledge_recall"]


def _recall(agent_dir, **kw):
    return build_knowledge_recall(agent_dir, FakeEmbedder(VECS), **kw)


# ── ① 端到端：相关进口径、无关不进；三路降级＝逐字节现状 ────────────────


def test_relevant_entry_enters_prompt_irrelevant_stays_out(fixture_db, kb_dir):
    llm = ScriptedLLM([Q1, _GOOD_SQL, "共 3 人"])  # understand 非 JSON＝回退原题意在题面稳定
    ans = run_question(fixture_db, Q1, llm=llm, settings=_S,
                       knowledge_recall=_recall(kb_dir))
    assert ans.failed is False and llm.calls == 3
    prompt = llm.prompts[1]  # generate
    assert KNOWLEDGE_HEADER in prompt and REL in prompt
    assert IRR not in prompt  # 正交得分 0＜阈值＝不进——也证明不是整档直塞


def test_no_wiring_baseline_vs_degradations_byte_identical(fixture_db, kb_dir, tmp_path):
    """空字典／缺 embedder／端点挂／缺档四路＝generate prompt 与无挂接基线逐字节
    等值（值链现状姊妹钉同形）；缺料不拦答题、llm.calls 不掺账。"""
    def _run(knowledge_recall=None):
        llm = ScriptedLLM([Q1, _GOOD_SQL, "共 3 人"])
        ans = run_question(fixture_db, Q1, llm=llm, settings=_S,
                           knowledge_recall=knowledge_recall)
        assert ans.failed is False and llm.calls == 3
        return llm.prompts

    base = _run()
    empty = tmp_path / "empty-kb"
    empty.mkdir()
    (empty / KNOWLEDGE_FILENAME).write_text("", encoding="utf-8")  # 空文件＝空池
    for rec in (_recall(empty),                          # 空字典
                build_knowledge_recall(kb_dir, None),    # 缺 embedder
                build_knowledge_recall(kb_dir, BoomEmbedder()),  # boom 模型名不合＝过期闸先拦
                _recall(tmp_path / "never-fed")):        # 缺档（现状）
        assert _run(rec) == base
    # （闸后真撞端点的挂法＝档与 embedder 模型名相合，入账半边见下面 ledger 测）


def test_pending_archive_degrades_to_empty(tmp_path):
    """挂账档（票 04 缺 embedder 落盘、无向量）读侧显形＝装载闸拒读→不注入
    ＋failed 入账点名挂账（消费面接住进料面的账，修法文案在票 04 报错单源里）。"""
    d = tmp_path / "no-vec"
    d.mkdir()
    src = tmp_path / "d.md"
    src.write_text(REL, encoding="utf-8")
    feed_knowledge(d, src, None)  # 挂账进料
    tp = tmp_path / "t.jsonl"
    tracer = TraceLogger(tp, run_id="r")
    assert _recall(d, tracer=tracer)(Q1) == ""
    row = _kb_rows(tp)[0]
    assert row["outcome"] == "failed" and "挂账" in row["reason"]


def test_degradation_ledger_rows_all_paths(tmp_path):
    """缺料入账可见（产物即开关的账目半边）：空池/缺 embedder 两 skipped/failed 行
    ＋闸后真撞端点（档与 embedder 模型名相合，BoomEmbedder.model 同名过闸）＋
    维度不合不外抛——各路皆""＝不注入、reason 点名（值链 test_broken_stale_boom 先例形制）。"""
    import yaml
    d = tmp_path / "kb4"
    d.mkdir()
    src = tmp_path / "m.md"
    src.write_text(REL, encoding="utf-8")
    feed_knowledge(d, src, FakeEmbedder(VECS))
    tp = tmp_path / "t.jsonl"
    tr = TraceLogger(tp, run_id="r")
    empty = tmp_path / "empty"
    empty.mkdir()
    (empty / KNOWLEDGE_FILENAME).write_text("", encoding="utf-8")
    assert _recall(empty, tracer=tr)(Q1) == ""      # 空池
    assert _kb_rows(tp)[-1]["outcome"] == "skipped" and "空档" in _kb_rows(tp)[-1]["reason"]
    assert build_knowledge_recall(d, None, tracer=tr)(Q1) == ""  # 缺 embedder
    assert _kb_rows(tp)[-1]["outcome"] == "failed" and "未配置" in _kb_rows(tp)[-1]["reason"]
    boom_dir = tmp_path / "kb-boom"
    boom_dir.mkdir()
    (boom_dir / KNOWLEDGE_FILENAME).write_text(yaml.safe_dump(  # 同名过闸＝真撞端点
        {"manifest": {"model_id": "boom-embed"},
         "entries": [{"text": REL, "vec": [1.0, 0.0]}]},
        allow_unicode=True), encoding="utf-8")
    emb = BoomEmbedder()
    assert build_knowledge_recall(boom_dir, emb, tracer=tr)(Q1) == ""
    row = _kb_rows(tp)[-1]
    assert row["outcome"] == "failed" and "向量化调用失败" in row["reason"]
    assert emb.calls == 1  # 过期闸放行＝这一发真发到端点才炸
    dim = tmp_path / "kb-dim"
    dim.mkdir()
    (dim / KNOWLEDGE_FILENAME).write_text(yaml.safe_dump(  # 模型名合、维度不合＝打分炸不拦答题
        {"manifest": {"model_id": "fake-embed"},
         "entries": [{"text": REL, "vec": [1.0, 0.0, 0.0]}]},
        allow_unicode=True), encoding="utf-8")
    assert _recall(dim, tracer=tr)(Q1) == ""
    assert _kb_rows(tp)[-1]["outcome"] == "failed" and "维度不合" in _kb_rows(tp)[-1]["reason"]
    assert all("input_tokens" not in r for r in _kb_rows(tp))  # 全族不烧生成调用数


# ── ② 账本形制＋memo＋过期闸 ───────────────────────────────────────────


def test_ledger_row_shape_no_generation_charge(fixture_db, kb_dir, tmp_path):
    tp = tmp_path / "traces.jsonl"
    tracer = TraceLogger(tp, run_id="r1")
    llm = ScriptedLLM([Q1, _GOOD_SQL, "共 3 人"])
    run_question(fixture_db, Q1, llm=llm, settings=_S, tracer=tracer,
                 knowledge_recall=_recall(kb_dir, tracer=tracer))
    rows = _kb_rows(tp)
    assert len(rows) == 1 and rows[0]["outcome"] == "ok"
    assert rows[0]["hits"] == 1 and rows[0]["pool"] == 2 and rows[0]["model"] == "fake-embed"
    assert "latency_s" in rows[0]
    # 不烧生成调用数：账本行无 token 标记（recall/value_link 行先例形制）
    assert "input_tokens" not in rows[0] and tracer.usage_for("-")["llm_calls"] == 3


def test_missing_archive_skips_with_repair_reason(fixture_db, tmp_path):
    tp = tmp_path / "traces.jsonl"
    tracer = TraceLogger(tp, run_id="r1")
    llm = ScriptedLLM([Q1, _GOOD_SQL, "共 3 人"])
    run_question(fixture_db, Q1, llm=llm, settings=_S, tracer=tracer,
                 knowledge_recall=_recall(tmp_path / "ghost", tracer=tracer))
    rows = _kb_rows(tp)
    assert len(rows) == 1 and rows[0]["outcome"] == "skipped"
    assert "knowledge-feed" in rows[0]["reason"]  # 一行给出可操作修法


def test_memo_retry_loop_single_embed_call(fixture_db, kb_dir):
    """执行失败→重试环二次 generate＝同题面 memo：向量化调用全程一发。"""
    emb = FakeEmbedder(VECS)
    llm = ScriptedLLM([Q1, "SELECT * FROM nope_table", _GOOD_SQL, "共 3 人"])
    ans = run_question(fixture_db, Q1, llm=llm, settings=_S,
                       knowledge_recall=build_knowledge_recall(kb_dir, emb))
    assert ans.failed is False and llm.calls == 4  # 重试环在跑（memo 不挡路）
    assert emb.calls == 1 and emb.batches == [[Q1]]
    assert llm.prompts[1].count(KNOWLEDGE_HEADER) == 1
    assert KNOWLEDGE_HEADER in llm.prompts[2]  # 重试轮同块再现（memo 复用非重召回）


def test_expired_model_gate_explicit(kb_dir, tmp_path):
    """票 04 移交在册：读侧过期判定必须**显式闸 stored.model_id**——同维跨模型
    向量不会自提示、字典路也没有关键词保底可兜。换模型 embedder＝整档作废＋入账。"""
    emb2 = FakeEmbedder(VECS)
    emb2.model = "fake-v2"  # 同维同值但不同模型——只有 model_id 闸看得见
    tp = tmp_path / "t.jsonl"
    tracer = TraceLogger(tp, run_id="r1")
    rec = build_knowledge_recall(kb_dir, emb2, tracer=tracer)
    assert rec(Q1) == ""
    row = _kb_rows(tp)[0]
    assert row["outcome"] == "failed" and "过期" in row["reason"]
    assert emb2.calls == 0  # 过期闸在 embed 前＝零花费不撞端点


# ── ② 优先级落位（图面）＋第四格恒等（select 钉）───────────────────────


def test_kb_then_examples_landing_and_state_residue_inert(fixture_db, kb_dir):
    """落位钉（ADR-0008 字典单通道）：字典块在 schema 后、参考例题前——例题守
    「贴用户问题最近」旧位不动（M9 论文形态钉）；状态残键 evidence 零注入防回吹。"""
    llm = ScriptedLLM([Q1, _GOOD_SQL, "共 3 人"])
    run_question(fixture_db, Q1, llm=llm, settings=_S,
                 knowledge_recall=_recall(kb_dir),
                 recall=lambda q: format_examples_block([("旧问", "SELECT 1")]))
    p = llm.prompts[1]
    i_schema, i_kb = p.index("## 数据库 Schema"), p.index(KNOWLEDGE_HEADER)
    i_ex, i_q = p.index(EXAMPLES_HEADER), p.index("## 用户问题")
    assert i_schema < i_kb < i_ex < i_q
    assert REL in p and "SELECT 1" in p and "背景信息" not in p


def test_select_fourth_slot_identity_and_non_generate_untouched():
    slots = {"question": Q1, "schema": "", "evidence": "", "failure_history": "",
             "draft": ""}
    assert gssc.select("generate", slots) is slots  # 双回调缺省＝原对象恒等
    assert gssc.select("generate", slots, knowledge_recall=lambda q: "") is slots
    assert gssc.select("understand", slots, knowledge_recall=lambda q: "X") is slots
    kb = format_knowledge_block([REL])
    out = gssc.select("generate", slots, knowledge_recall=lambda q: kb)
    assert out["knowledge"] == kb and "knowledge" not in slots  # 新 dict 不脏原槽


# ── ④ 渲染/阈值/常量/布线钉 ────────────────────────────────────────────


def test_block_rendering_preserves_entry_shape():
    assert format_knowledge_block([]) == ""  # 空＝逐字节现状
    block = format_knowledge_block(["# 不良率口径\n逾期 90 天以上贷款余额占比。"])
    lines = block.splitlines()
    assert lines[0] == KNOWLEDGE_HEADER
    assert lines[1] == "- # 不良率口径"
    assert lines[2] == "  逾期 90 天以上贷款余额占比。"  # 块内换行缩进承接＝保形不美化


def test_top_k_caps_entries_in_feed_order(tmp_path):
    """条数保守值＝top-K 截尾；同分 stable argsort 保档内进料序（确定性）。"""
    d = tmp_path / "many"
    d.mkdir()
    entries = [f"口径{i}" for i in range(5)]
    table = {t: [1.0, 0.0] for t in entries}
    table["问？"] = [1.0, 0.0]
    src = tmp_path / "m.md"
    src.write_text("\n\n".join(entries), encoding="utf-8")
    feed_knowledge(d, src, FakeEmbedder(table))
    block = build_knowledge_recall(d, FakeEmbedder(table))("问？")
    lines = [ln[2:] for ln in block.splitlines() if ln.startswith("- ")]
    assert lines == entries[:KNOWLEDGE_TOP_K]


def test_threshold_filters_orthogonal_noise(tmp_path):
    """绝对二道闸只挡正交噪声（票 00 教训：绝对阈不可靠＝取低档宁漏杀不误杀）。"""
    d = tmp_path / "kb2"
    d.mkdir()
    src = tmp_path / "m.md"
    src.write_text("近义口径\n\n正交口径", encoding="utf-8")
    table = {"近义口径": [0.9, 0.435], "正交口径": [0.0, 1.0], "问": [1.0, 0.0]}
    feed_knowledge(d, src, FakeEmbedder(table))
    block = build_knowledge_recall(d, FakeEmbedder(table))("问")
    assert "近义口径" in block and "正交口径" not in block  # ≈0.9 进、0 出局


def test_constants_and_calibration_note():
    assert KNOWLEDGE_TOP_K == 5      # 票 07 定标（k3→k5 召回 7/10→8/10，免费网格在册）
    assert KNOWLEDGE_MIN_SCORE == 0.45  # 票 07 定标（0.35-0.55 召回不敏感→取中挡噪）
    assert KNOWLEDGE_HEADER.startswith("## 口径字典片段")
    src = inspect.getsource(knowledge_mod)
    assert src.count("票 07 定标") >= 2  # 两旋钮定标注释皆在册（票面计数钉，值链同族）
    assert "票 07 已定标" in src  # 防漂移锚（值链姊妹钉同法：子串计数会被「待」字旧形态蒙过）


def test_eval_wired_cli_ask_not():
    """字典验收必须经 eval（spec §二 Q8 两义分家——「eval 零触」钉只守人签题对，
    本钉生效面）；CLI ask 不挂＝现状（表卡/值链裁决同族）。"""
    import qadata.cli.main as cli_main
    import qadata.eval.bird as eval_bird

    assert "build_knowledge_recall" in inspect.getsource(eval_bird)
    assert "build_knowledge_recall" not in inspect.getsource(cli_main)


def test_knowledge_recall_not_a_state_key():
    assert "knowledge_recall" not in AgentState.__annotations__  # 可调用不进状态键


# ── ② 口径通道钉（web 层）：字典块唯一注入＋请求残键惰性（ADR-0008）──


def test_kb_channel_web_and_evidence_residue_inert(store, fixture_db, tmp_path):
    """web 面（ADR-0008）：字典块＝口径唯一注入通道；老客户端多传 evidence 请求残键
    ＝pydantic 未知键忽略、零注入不报错（退役键读取忽略先例的请求面同款）。"""
    a = _agent_with_datasource(store, fixture_db)
    entry = "字典口径：贷款状态 'A' 计为正常。"
    src = tmp_path / "d.md"
    src.write_text(entry, encoding="utf-8")
    llm = ScriptedLLM(_HAPPY_SCRIPT)  # understand 改写输出＝"改写"（消解后题面＝召回题料）
    table = {"改写": [1.0, 0.0], entry: [1.0, 0.0]}
    feed_knowledge(store.agent_dir(a.id), src, FakeEmbedder(table))
    tp = tmp_path / "traces.jsonl"
    tracer = TraceLogger(tp, run_id="r9")
    client = TestClient(create_app(llm=llm, settings=_S, agents=store, tracer=tracer,
                                   static_dir="__no_such_dist_for_tests__",
                                   embedder=FakeEmbedder(table)))
    r = client.post("/api/ask", json={"agent_id": a.id, "question": "有几个学生",
                                      "evidence": "会话口径：显式输入"})
    assert r.json()["failed"] is False
    p = llm.prompts[1]
    assert "会话口径：显式输入" not in p       # 退役请求键零注入（不报错、不生效）
    assert KNOWLEDGE_HEADER in p              # 字典块＝唯一口径料
    assert p.index("## 数据库 Schema") < p.index(KNOWLEDGE_HEADER) < p.index("## 用户问题")
    assert "字典口径" in p
    assert _kb_rows(tp)[0]["outcome"] == "ok"
