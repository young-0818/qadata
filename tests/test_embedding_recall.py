"""M9 票 06 专测：例题库（人签）＋ hybrid embedding 召回（Select 格上岗，离线零联网）。

钉票面验收全表：
① FakeEmbedder 离线钉：调用计数（批量签库＝一次、每问一发、题面 memo 重试不翻倍）、
  top-K 形状、**升序注入**（最像的贴问题最近）、hybrid 关键词精确保底命中；
② 例题库档形状与人签纪律：signed_by 装载闸（自动吸收在机制上无路可走——无签拒收
  ＋写入口零触扫描：ask 链路/会话/反馈/CLI/eval 源码出现 sign_examples 进料即红）；
③ 人签一对题端到端：召回进 generate prompt、账本 recall 行（「一次向量化调用」本体、
  无 token 标记＝不烧调用数）；
④ 端点故障注入＝不召回＋降级入账不静默、本轮照常作答；坏档/未配置同纪律；
⑤ 空池/未挂接＝逐字节现状姊妹钉（FakeEmbedder 零碰、prompt 无例题面）；
⑥ 注入料吃票 04 保险丝：淘汰序新增「撤参考例题」（摘要面后、值采样前）；
⑦ 配置面（QADATA_EMBED_MODEL）在 test_config 钉。零 eval 花费：默认空池零触发。
"""
import inspect
import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from qadata.graph import gssc
from qadata.graph.prompts import (
    EXAMPLES_HEADER,
    format_examples_block,
    sql_prompt,
)
from qadata.llm.tracing import TraceLogger
from qadata.tools.schema import VALUE_SAMPLE_HEADER
from qadata.web.agents import AgentStore
from qadata.web.app import create_app
from qadata.web.examples import (
    RECALL_TOP_K,
    Example,
    ExampleError,
    build_recall,
    load_examples,
    sign_examples,
)
from tests.fakes import BoomEmbedder, FakeEmbedder, ScriptedLLM
from tests.test_web_api import _HAPPY_SCRIPT, _S, _agent_with_datasource


@pytest.fixture
def store(tmp_path):
    return AgentStore(tmp_path / "agents", metrics_dir=tmp_path / "metrics")


def _pair(q, sql, vec, by="owner"):
    return {"q": q, "sql": sql, "signed_by": by, "vec": list(vec)}


def _ex(q, sql, vec):
    return Example(q=q, sql=sql, signed_by="owner", vec=tuple(vec))


# ── ① hybrid：向量面漏的精确标识符，关键词保底捞回 ─────────────────────


def test_hybrid_keyword_floor_beats_vector_miss():
    """纯向量面 exA（dot 0.855）碾压 exB（dot −0.9 的**负相关**）；题面含精确标识符
    "loan" 命中 exB 的问/SQL → 命中作第一排序键的硬保底把它顶进召回——AskTable 业界
    修正的机制本体（双轴评审追补：加法权重在 dot 全幅下不是严格下界，字典序才是）。"""
    exA = _ex("学生成绩统计", "SELECT AVG(score) FROM scores", (0.95, 0.0))
    exB = _ex("loan 表账户数", "SELECT COUNT(*) FROM loan", (-1.0, 0.0))
    emb = FakeEmbedder({"loan 账户数量统计": [0.9, 0.1]})
    recall = build_recall([exA, exB], emb, top_k=1)
    block = recall("loan 账户数量统计")
    assert block == format_examples_block([(exB.q, exB.sql)])  # 向量输家反而入选
    assert exA.q not in block
    assert emb.calls == 1 and emb.batches == [["loan 账户数量统计"]]  # 一次向量化调用


def test_top_k_and_ascending_injection():
    """top-K 封顶取高、注入序＝相似度升序（最像的排最后、贴问题最近——DB-GPT 形态）。"""
    pool = [_ex(f"问{i}", f"SELECT {i}", (v,))
            for i, v in [(1, 0.9), (2, 0.5), (3, 0.7), (4, 0.1)]]
    emb = FakeEmbedder({"哪个班人最多": [1.0]})
    recall = build_recall(pool, emb)
    block = recall("哪个班人最多")
    assert "问4" not in block  # 第 4 名出局（top_k 默认 3）
    assert block.index("问2") < block.index("问3") < block.index("问1")  # 升序：0.5→0.7→0.9
    assert block.startswith(EXAMPLES_HEADER)


def test_recall_memo_one_vectorize_per_question():
    """同题重装配（generate 重试环/精准模式）＝memo 命中，向量化只发一次。"""
    emb = FakeEmbedder({"甲题": [1.0], "乙题": [0.5]})
    recall = build_recall([_ex("历史问", "SELECT 1", (1.0,))], emb)
    assert recall("甲题") == recall("甲题") and recall("乙题")
    assert emb.calls == 2


def test_recall_ledger_shape_does_not_burn_call_count(tmp_path):
    """账本＝「一次向量化调用」本体：outcome 行无 token 标记＝不进 llm_calls 聚合
    （budget_fuse/metric_match outcome 先例）；model/pool/k 如实。"""
    tracer = TraceLogger(tmp_path / "traces.jsonl", run_id="r6")
    emb = FakeEmbedder({"题": [1.0]})
    build_recall([_ex("历", "SELECT 1", (1.0,))], emb, tracer=tracer)("题")
    rows = [json.loads(ln) for ln in
            (tmp_path / "traces.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1 and rows[0]["node"] == "recall"
    assert rows[0]["outcome"] == "ok" and rows[0]["hits"] == 1 and rows[0]["pool"] == 1
    assert rows[0]["model"] == "fake-embed" and "input_tokens" not in rows[0]
    assert tracer.usage_for("-")["llm_calls"] == 0


def test_default_constants_are_the_pinned_conversion():
    assert RECALL_TOP_K == 3


# ── ② 例题库档形状与人签纪律（自动吸收在机制上无路可走）────────────────


def test_sign_roundtrip_re_sign_and_skip(tmp_path):
    d = tmp_path / "agent"
    d.mkdir()
    emb = FakeEmbedder({"问甲": [1.0, 2.0], "问乙": [0.5, 0.5], "问甲v2": [0.0, 1.0]})
    n, skipped = sign_examples(d, [
        {"q": "问甲", "sql": "SELECT 1", "signed_by": "owner A"},
        {"q": "问乙", "sql": "SELECT 2", "signed_by": "owner A"},
        {"q": "坏行", "sql": "SELECT 3"},  # 无签＝拒（人签纪律）
    ], emb.embed)
    assert (n, skipped) == (2, 1) and emb.calls == 1  # 批量向量化＝一次调用
    bank = load_examples(d)
    assert [e.q for e in bank] == ["问甲", "问乙"]
    assert bank[0].vec == (1.0, 2.0) and bank[0].signed_by == "owner A"
    n2, _ = sign_examples(d, [{"q": "问甲", "sql": "SELECT 9", "signed_by": "owner B"}],
                          emb.embed)
    bank = load_examples(d)  # 同题重签＝覆盖（后签为真），无第二份
    assert n2 == 1 and len(bank) == 2 and bank[0].sql == "SELECT 9"


def test_loader_requires_human_signature(tmp_path):
    """成功轮载荷形状（question/sql，无 signed_by）灌不进装载闸——「不自动吸收」的
    机制面：机器生成的条目永远不会带人签。坏向量列同拒。"""
    d = tmp_path / "agent"
    d.mkdir()
    (d / "examples.yaml").write_text(yaml.safe_dump(
        [{"q": "自动吸收 attempt", "sql": "SELECT 1", "vec": [0.1]}],
        allow_unicode=True), encoding="utf-8")
    with pytest.raises(ExampleError, match="signed_by"):
        load_examples(d)
    (d / "examples.yaml").write_text(yaml.safe_dump(
        [_pair("甲", "SELECT 1", [0.1]) | {"vec": []}], allow_unicode=True),
        encoding="utf-8")
    with pytest.raises(ExampleError, match="向量"):
        load_examples(d)
    (d / "examples.yaml").write_text(yaml.safe_dump(
        [{"q": "甲", "sql": "SELECT 1", "signed_by": "o"}], allow_unicode=True),
        encoding="utf-8")
    with pytest.raises(ExampleError, match="向量"):  # 缺 vec 键＝坏档，不是 KeyError
        load_examples(d)


def test_sign_refuses_orphan_dir(tmp_path):
    emb = FakeEmbedder({"问甲": [1.0]})
    with pytest.raises(ExampleError, match="目录不存在"):
        sign_examples(tmp_path / "ghost", [{"q": "问甲", "sql": "SELECT 1",
                                           "signed_by": "owner"}], emb.embed)


def test_auto_absorption_mechanically_refused():
    """写入口零触扫描：ask 链路/会话/反馈/图侧源码出现 sign_examples 进料即红；
    CLI 的 examples-sign（唯一进料口）之外，问数调用面零挂 recall、eval 零触例题面。
    （M10 票 01 两义分家：本钉守的是**人签知识不进自动评测**（防自动吸收病灶）——
    eval 面禁词从泛指 recall/embed 收窄为例题专名 build_recall/examples；库派生物
    （表卡/值索引）的验收必须经 eval（ADR-0004／spec §五「扩用被否」），CLI ask 面
    依旧零挂（"recall" 整词钉不动——表卡粗召回属 serve/eval 面，ask 现状路径）。）"""
    import qadata.cli.main as cli_main
    import qadata.eval.bird as eval_bird
    import qadata.graph.build as build_mod
    import qadata.graph.nodes as nodes_mod
    import qadata.web.app as app_mod
    import qadata.web.feedback as feedback_mod
    import qadata.web.sessions as sessions_mod

    for mod in (app_mod, sessions_mod, feedback_mod, nodes_mod, build_mod, eval_bird):
        assert "sign_examples" not in inspect.getsource(mod), mod.__name__
    assert "recall" not in inspect.getsource(cli_main)
    for name in ("build_recall", "examples"):
        # 例题进料/召回通道零触 eval——表卡路的符号是 build_table_recall/embedder，
        # 词形不同即分家本身（向量化通道 eval 照建＝库派生物验收的既定义务）
        assert name not in inspect.getsource(eval_bird), name


# ── ③ 端到端：人签一对题 → 召回进 prompt（票面人工核验的测形替身）───────


def _client6(llm, store, tracer=None, embedder=None):
    return TestClient(create_app(llm=llm, settings=_S, agents=store, tracer=tracer,
                                 static_dir="__no_such_dist_for_tests__",
                                 embedder=embedder))


def test_signed_pair_reaches_generate_prompt(store, fixture_db, tmp_path):
    """签两对题 → 单问端到端：例题块进 generate（不进水路其余）、升序注入、
    零新增生成调用、账本一行「一次向量化调用」。"""
    a = _agent_with_datasource(store, fixture_db)
    emb = FakeEmbedder({"问学生数": [0.5], "学生人数统计": [0.9], "改写": [1.0]})
    sign_examples(Path(store.agent_dir(a.id)),
                  [{"q": "问学生数", "sql": "SELECT COUNT(*) FROM students",
                    "signed_by": "owner"},
                   {"q": "学生人数统计", "sql": "SELECT COUNT(id) FROM students",
                    "signed_by": "owner"}], emb.embed)
    assert emb.calls == 1  # 签库＝批量一次
    tracer = TraceLogger(tmp_path / "traces.jsonl", run_id="r6")
    llm = ScriptedLLM(_HAPPY_SCRIPT)
    r = _client6(llm, store, tracer, emb).post(
        "/api/ask", json={"agent_id": a.id, "question": "有几个学生"})
    assert r.json()["failed"] is False
    assert llm.calls == 3  # 零新增生成调用（understand/generate/respond 照旧）
    gp = llm.prompts[1]  # 只有 generate 吃召回
    assert EXAMPLES_HEADER in gp and llm.prompts[0].count(EXAMPLES_HEADER) == 0
    assert gp.index("问学生数") < gp.index("学生人数统计") < gp.index("## 用户问题")
    assert emb.calls == 2  # 签 1＋问 1
    rows = [json.loads(ln) for ln in
            (tmp_path / "traces.jsonl").read_text(encoding="utf-8").splitlines()]
    rec = [x for x in rows if x["node"] == "recall"]
    assert len(rec) == 1 and rec[0]["outcome"] == "ok" and rec[0]["hits"] == 2


def test_generate_retry_does_not_double_vectorize(store, fixture_db):
    """执行失败→重试环：同题再装配＝memo 命中，向量化不逐次翻倍（账形在册）。"""
    a = _agent_with_datasource(store, fixture_db)
    emb = FakeEmbedder({"问学生数": [0.5], "改写": [1.0]})
    sign_examples(Path(store.agent_dir(a.id)),
                  [{"q": "问学生数", "sql": "SELECT COUNT(*) FROM students",
                    "signed_by": "owner"}], emb.embed)
    llm = ScriptedLLM(_HAPPY_SCRIPT[:1] + ["SELECT missing_tbl"] + _HAPPY_SCRIPT[1:])
    r = _client6(llm, store, None, emb).post(
        "/api/ask", json={"agent_id": a.id, "question": "有几个学生"})
    assert r.json()["failed"] is False and llm.calls == 4  # 重试一环照旧
    assert llm.prompts[1].count(EXAMPLES_HEADER) == llm.prompts[2].count(EXAMPLES_HEADER) == 1
    assert emb.calls == 2  # 签 1＋问 1（不是 签1＋问2）


# ── ④ 失败＝降级不召回＋入账不静默 ─────────────────────────────────────


def test_embed_boom_degrades_answer_stands(store, fixture_db, tmp_path):
    a = _agent_with_datasource(store, fixture_db)
    emb = FakeEmbedder({"问甲": [1.0], "改写": [1.0]})
    sign_examples(Path(store.agent_dir(a.id)),
                  [{"q": "问甲", "sql": "SELECT 1", "signed_by": "owner"}], emb.embed)
    tracer = TraceLogger(tmp_path / "traces.jsonl", run_id="r6")
    llm = ScriptedLLM(_HAPPY_SCRIPT)
    r = _client6(llm, store, tracer, BoomEmbedder()).post(
        "/api/ask", json={"agent_id": a.id, "question": "有几个学生"})
    assert r.json()["failed"] is False  # 不拦答题
    assert EXAMPLES_HEADER not in llm.prompts[1]
    rows = [json.loads(ln) for ln in
            (tmp_path / "traces.jsonl").read_text(encoding="utf-8").splitlines()]
    bad = [x for x in rows if x["node"] == "recall"]
    assert len(bad) == 1 and bad[0]["outcome"] == "failed"
    assert "向量化调用失败" in bad[0]["reason"] and "input_tokens" not in bad[0]


def test_broken_bank_and_unconfigured_embedder_logged_not_silent(store, fixture_db,
                                                                 tmp_path):
    """坏档/池在而向量化未配置＝同纪律：入账、照常、不静默；空池则零行（默认关）。"""
    a = _agent_with_datasource(store, fixture_db)
    d = Path(store.agent_dir(a.id))
    (d / "examples.yaml").write_text("这不是列表", encoding="utf-8")
    tracer = TraceLogger(tmp_path / "traces.jsonl", run_id="r6")
    r = _client6(ScriptedLLM(_HAPPY_SCRIPT), store, tracer).post(
        "/api/ask", json={"agent_id": a.id, "question": "有几个学生"})
    assert r.json()["failed"] is False
    rows = [json.loads(ln) for ln in
            (tmp_path / "traces.jsonl").read_text(encoding="utf-8").splitlines()]
    assert "例题库形状不正" in next(x for x in rows
                                      if x["node"] == "recall")["reason"]  # 前缀单源不套双份
    (d / "examples.yaml").write_text(yaml.safe_dump(
        [_pair("问甲", "SELECT 1", [0.5])], allow_unicode=True), encoding="utf-8")
    r = _client6(ScriptedLLM(_HAPPY_SCRIPT), store, tracer).post(
        "/api/ask", json={"agent_id": a.id, "question": "有几个学生"})
    assert r.json()["failed"] is False  # embedder 缺位＝入账不召回
    rows = [json.loads(ln) for ln in
            (tmp_path / "traces.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [x for x in rows if x["node"] == "recall"][-1]["reason"] == \
        "向量化未配置（QADATA_EMBED_MODEL 为空）"


# ── ⑤ 空池/未挂接＝逐字节现状姊妹钉 ────────────────────────────────────


def test_empty_bank_byte_identical_current(store, fixture_db, tmp_path):
    """无例题库档（默认形态）：假向量化器零碰、generate prompt 无例题面、账本零行。"""
    a = _agent_with_datasource(store, fixture_db)
    emb = FakeEmbedder({})
    tracer = TraceLogger(tmp_path / "traces.jsonl", run_id="r6")
    llm = ScriptedLLM(_HAPPY_SCRIPT)
    r = _client6(llm, store, tracer, emb).post(
        "/api/ask", json={"agent_id": a.id, "question": "有几个学生"})
    assert r.json()["failed"] is False and llm.calls == 3
    assert emb.calls == 0 and EXAMPLES_HEADER not in llm.prompts[1]
    assert not (tmp_path / "traces.jsonl").exists() or \
        "recall" not in (tmp_path / "traces.jsonl").read_text(encoding="utf-8")


def test_select_identity_when_unwired_or_empty_block():
    slots = gssc.gather_generate({"question": "Q", "evidence": "", "db_schema": "S",
                                  "attempts": [], "verify_note": None,
                                  "session_context": None})
    assert gssc.select("generate", slots, recall=lambda q: "") is slots  # 空块＝原对象
    touched = []
    assert gssc.select("understand", {}, recall=lambda q: touched.append(q)) == {}
    assert not touched  # 非 generate 场景＝回调不冒泡（只有 SQL 生成吃例题）
    # 未挂接＝逐字节现状（空池＝默认关的装配面本体，金标准 oracle 同串）
    assert gssc.assemble("generate", slots) == sql_prompt(schema="S", evidence="",
                                                          question="Q")


# ── ⑥ 注入料吃保险丝：淘汰序新增「撤参考例题」（摘要后、值采样前）────────


def test_fuse_evicts_examples_before_value_samples(monkeypatch):
    """注入料吃预算格兜底（票 04×06 咬合）：撤参考例题排在摘要面之后、值采样之前
    ——few-shot 是最可再生的资料类，schema 素材是本题答案的地板。"""
    monkeypatch.setitem(gssc.FUSE_TOKENS, "generate", 1)  # 必超：淘汰链走到位
    slots = dict(gssc.gather_generate({"question": "Q" * 50, "evidence": "",
                                        "db_schema": "S\n" + VALUE_SAMPLE_HEADER + "值料",
                                        "attempts": [], "verify_note": None,
                                        "session_context": None}),
                 examples=format_examples_block([("历", "SELECT 1")]))
    info = {}
    out = gssc.compress("generate", gssc.structure("generate", slots),
                        on_compress=info.update)
    assert "撤参考例题" in info["actions"]
    assert info["actions"].index("撤参考例题") < info["actions"].index("砍值采样")
    assert EXAMPLES_HEADER not in out


def test_examples_zone_landing_in_generate():
    slots = dict(gssc.gather_generate({"question": "Q", "evidence": "", "db_schema": "S",
                                       "attempts": [], "verify_note": None,
                                       "session_context": None}),
                 examples=EXAMPLES_HEADER + "\n问：历\nSQL：SELECT 1")
    zones = [s.zone for s in gssc.structure("generate", slots)]
    assert zones == [gssc.Zone.ROLE, gssc.Zone.EVIDENCE, gssc.Zone.EVIDENCE,
                     gssc.Zone.EVIDENCE, gssc.Zone.TASK, gssc.Zone.STATE,
                     gssc.Zone.MEMORY, gssc.Zone.OUTPUT]
