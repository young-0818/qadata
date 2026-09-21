"""M9 票 04 专测：tiktoken 预算保险丝＋确定性淘汰序（Compress 总闸）。

钉五件事（票面验收全表）：
① 评测/现实最坏形态逐字节一致＝保险丝永不触发的证明（BIRD 式链路＋web 会话满窗
  形态双跑 oracle，降级回调零触发；计数余量本身有数值钉——定阈证据落档
  .scratch/qadata-m9/ticket04-threshold.md）；
② 人为灌爆：淘汰序逐级生效顺序钉死——先最老记忆行、次值采样、再失败历史，
  任务与输出分区永不砍；无料可砍＝如实入账不硬砍；
③ 词表缺失＝如实炸（装配时＋load_settings 启动位点），无网络兜底无 len 兜底；
   词表快照确定性有计数数值钉（换/坏文件即红，零联网）；
④ 降级事件进审计账本（budget_fuse 行、不计为 LLM 调用）与 trace（tool 胶囊帧＝
  票 01 出口形状，镜像成 span、回放 trail 自动承接）；
⑤ 挂接布线：nodes×4＋schema×1 全带 on_compress——摘线即红。
"""
import json
from pathlib import Path

import pytest
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from qadata.config import Settings, load_settings
from qadata.graph import gssc
from qadata.graph.build import run_question
from qadata.graph.gssc import count_tokens
from qadata.graph.metrics import load_registry
from qadata.graph.prompts import (
    _PICK_PROMPT,
    TRUNCATION_HINT,
    format_failure_history,
    format_session_draft,
    format_session_history,
    metric_review_prompt,
    respond_prompt,
    sql_prompt,
    understand_prompt,
)
from qadata.llm.tracing import TraceLogger
from qadata.obs import Obs
from qadata.tools.schema import VALUE_SAMPLE_HEADER
from qadata.types import QueryResult, SqlAttempt
from tests.fakes import ScriptedLLM

_S = Settings(api_key="", base_url="", model="test-model", retry_budget=3)
_GOOD = "SELECT name FROM students WHERE id = 2"

# 满窗记忆（K=5 现实最大形态：BIRD SQL 长度级行＋摘要，末轮失败＝最保守行形）
_TURNS = [{"question": f"20{20 + i} 年各月贷款违约户数是多少，按月份分组列出",
           "sql": "SELECT strftime('%Y-%m', loan.date) m, COUNT(*) FROM loan "
                  "WHERE loan.status IN ('B','N') GROUP BY m ORDER BY m",
           "row_count": 12, "head": "头部 3 行：1998-01 | 3；1998-02 | 5；1998-03 | 2",
           "failed": i == 4} for i in range(5)]
_CTX = {"turns": _TURNS, "draft": {"sql": _TURNS[-1]["sql"], "head": _TURNS[-1]["head"]}}
_EV = "违约率 = 状态为'B'或'B'的贷款笔数除以全部贷款笔数；日期口径按 loan.date；" \
      "百分比保留两位小数；金额单位为千欧元（原始数据除以1000）" * 3


# ── ① 现实最坏形态：逐字节一致＋永不触发＋余量数值钉 ────────────────────


def _no_trigger(scenario, slots, oracle):
    """限内＝恒等：prompt 逐字节等 oracle、降级回调零触发；返回实测计数。"""
    events = []
    out = gssc.assemble(scenario, slots, on_compress=events.append)
    assert out == oracle, scenario
    assert events == [], f"{scenario} 现实形态不得触发保险丝"
    tok = count_tokens(out)
    assert tok <= gssc.FUSE_TOKENS[scenario], scenario
    return tok


def test_understand_realistic_form_never_triggers():
    q = "去年这些账户的违约户有多少"
    assert _no_trigger("understand",
                       gssc.gather_understand({"question": q, "evidence": _EV,
                                               "session_context": _CTX}, clarify=True),
                       understand_prompt(q, _EV, format_session_history(_CTX),
                                         clarify=True)) > 300


def _worst_schema() -> str:
    """generate 现实最大 schema 料：全量阈值 8000 字级 DDL＋列注释＋满额值采样块。"""
    ddl = "\n\n".join(
        f"CREATE TABLE t{i} (\n  id INTEGER PRIMARY KEY,\n  name TEXT,\n"
        f"  amount REAL,\n  op_date TEXT,\n  district_id INTEGER,\n  note TEXT\n)"
        for i in range(50))
    desc = "\n".join("- name：客户姓名｜户主姓名，可能含空格" for _ in range(30))
    samples = (VALUE_SAMPLE_HEADER + "\n"
               + "\n".join(f"[t{i}] - op_date：'1993-07-05'｜'1997-12-10'（窗口内前 3 值）"
                           for i in range(50)))
    return f"{ddl}\n\n表 t0 列注释：\n{desc}\n\n{samples}"


def test_generate_realistic_form_never_triggers():
    attempts = [SqlAttempt(sql="SELECT nope FROM t0", error="no such table: nope"),
                SqlAttempt(sql="SELECT a FROM t0, t1",
                           error="ambiguous column name: a"),
                SqlAttempt(sql="SELECT amount FROM t99", error="no such table: t99")]
    q = "1993 年开设的账户中，违约贷款的平均金额是多少（千欧元）"
    vn = "结果为空，可能筛选条件过严"
    schema = _worst_schema()
    assert _no_trigger("generate",
                       gssc.gather_generate({"question": q, "evidence": _EV,
                                             "db_schema": schema, "attempts": attempts,
                                             "verify_note": vn, "metric_note": None,
                                             "session_context": _CTX}),
                       sql_prompt(schema=schema, evidence=_EV, question=q,
                                  history=format_failure_history(attempts, vn, None),
                                  draft=format_session_draft(_CTX))) > 3000


def test_metric_match_realistic_form_never_triggers():
    metrics = load_registry(Path("metrics/financial.yaml"))  # 票 04 定稿 18 条整表
    assert len(metrics) == 18
    q = "去年各季度贷款违约率是多少，用百分比表示"
    assert _no_trigger("metric_match",
                       gssc.gather_metric_review({"question": q, "evidence": _EV},
                                                 metrics),
                       metric_review_prompt(q, _EV, metrics)) > 300


def test_respond_realistic_form_never_triggers():
    cols = [f"c{i}" for i in range(5)]
    rows = [("1993-07-05 布拉格西区账户甲", 12345.678, "长期违约状态B类", "x" * 60, i)
            for i in range(10)]
    res = QueryResult(columns=cols, rows=rows, row_count=9876,
                      truncated=True, elapsed_ms=42)
    rows_table = (" | ".join(cols) + "\n"
                  + "\n".join(" | ".join(str(v) for v in r) for r in rows))
    sql = "SELECT a.b, c.d FROM long_join_query a JOIN b ON a.id = b.id"
    assert _no_trigger("respond",
                       gssc.gather_respond({"question": "列出前十大违约账户"},
                                           result=res, sql=sql,
                                           rows_table=rows_table, n=10),
                       respond_prompt("列出前十大违约账户", sql,
                                      rows_table + TRUNCATION_HINT, 9876, 10)) > 300


def test_explore_realistic_form_never_triggers():
    tables = [f"table_name_{i}_with_some_length" for i in range(20)]
    q = "哪些表的金额列存在大小写不一致的存储形态"
    assert _no_trigger("explore", gssc.gather_schema_pick(tables, q),
                       _PICK_PROMPT.format(tables=", ".join(tables), question=q)) > 100


def test_bird_fixture_end_to_end_byte_equal(fixture_db):
    """BIRD 式固定链路（假模型零联网零现金）：真装配器出口的 prompt 与旧路 oracle
    逐字节一致——评测形态零变化的端到端背书（行为零变化不需配对）。"""
    llm = ScriptedLLM(['{"question":"查学生数"}', "SELECT COUNT(*) FROM students",
                       "共 2 人"])
    ans = run_question(fixture_db, "有多少学生", llm=llm, settings=_S, evidence=_EV)
    assert ans.failed is False and llm.calls == 3
    assert llm.prompts[0] == understand_prompt("有多少学生", _EV,
                                               format_session_history(None))


# ── ② 灌爆料池：淘汰序逐级生效顺序钉死 ──────────────────────────────────


def test_eviction_order_memory_then_samples_then_history(monkeypatch):
    """满料 generate：草稿→值采样→失败历史逐单位淘汰，序钉死；永不砍面原样在位。"""
    schema = ("CREATE TABLE a(b TEXT);\n" * 200 + "\n"
              + VALUE_SAMPLE_HEADER + "\n" + "- b：'x'｜'y'\n" * 40)
    attempts = [SqlAttempt(sql=f"SELECT {i} FROM nope_{i}", error="no such table")
                for i in range(3)]
    monkeypatch.setitem(gssc.FUSE_TOKENS, "generate", 600)  # 逼到三级全走一遍才停
    events = []
    slots = gssc.gather_generate({
        "question": "有几名学生", "evidence": "", "db_schema": schema,
        "attempts": attempts, "verify_note": None, "metric_note": None,
        "session_context": {"turns": [],
                            "draft": {"sql": "SELECT " + "draft_body " * 60,
                                      "head": "标量值 3"}}})
    out = gssc.assemble("generate", slots, on_compress=events.append)
    assert events[0]["actions"] == ["整段撤记忆", "砍值采样",
                                    "缩失败历史", "缩失败历史", "缩失败历史"], \
        "逐级生效序＝记忆（无块结构则整段、动作如实分名）→值采样→失败历史，一步一单位"
    assert events[0]["before"] > 600 and events[0]["after"] < events[0]["before"]
    assert events[0]["after"] > 600, "无料可砍后仍超限＝如实停手不硬砍（schema 本体不砍）"
    assert "draft_body" not in out and VALUE_SAMPLE_HEADER not in out
    assert "no such table" not in out  # 三条失败块逐级删光，无料即停（不硬砍）
    assert out.startswith("你是一个严谨的数据分析 SQL 专家")  # 角色永不砍
    assert "## 用户问题\n有几名学生" in out and out.endswith("输出一条 SQL：")
    assert "CREATE TABLE a" in out  # schema 本体（非值采样部分）不在淘汰序


def test_memory_drops_oldest_first_one_line_at_a_time(monkeypatch):
    """understand：逐单位淘汰必从最老行开始（时间升序＝列表头即最老），最新行最后倒。
    阈值取「满量−1 行」量级（按实测计数推导，非魔法数）——钉住逐行、非整段一把砍。"""
    slots = gssc.gather_understand({"question": "这些呢", "evidence": "口径",
                                    "session_context": _CTX}, clarify=False)
    full = count_tokens(gssc.assemble("understand", slots))
    monkeypatch.setitem(gssc.FUSE_TOKENS, "understand", full - 120)  # 容得下最新几行
    events = []
    out = gssc.assemble("understand", slots, on_compress=events.append)
    assert events and all(a == "丢最老记忆行" for a in events[0]["actions"])
    assert 1 <= len(events[0]["actions"]) < 6, "部分淘汰＝钉住逐单位机制"
    kept = [ln for ln in out.splitlines() if ln.startswith("- 问：")]
    assert 0 < len(kept) < 5, "部分淘汰＝钉住逐行机制"
    assert "2020 年" not in kept[0] and "2024 年" in kept[-1]  # 最老先丢、最新守住
    assert "## 会话历史" in out and "原始问题：这些呢" in out  # 节头与任务面在位


def test_full_eviction_chain_includes_sticker_and_knowledge(monkeypatch):
    """M10 票 03 扩钉、票 05 再扩（全灌爆走满淘汰序）：记忆→值纸条→字典块→参考例题→
    值采样→失败历史。撤值纸条位挨着撤字典块（库派生物比人签料更可再生＝更先出局）；
    撤字典块在撤参考例题**之前**——判据在册 gssc._cut_knowledge（字典档恒在、逐问
    免费重召回 vs signed_by 人签稀缺资产）；纸条恒贴 schema 最末，split 切尾不连累
    值采样块（各刀安位由序保证）。"""
    from qadata.graph.prompts import EXAMPLES_HEADER, format_examples_block
    from qadata.retrieval.knowledge import format_knowledge_block
    from qadata.retrieval.values import VALUE_STICKER_HEADER
    schema = ("CREATE TABLE a(b TEXT);\n" * 20 + "\n"
              + VALUE_SAMPLE_HEADER + "\n" + "- b：'x'｜'y'\n" * 8 + "\n"
              + VALUE_STICKER_HEADER + "\n" + "- a.b —— 库里实际这么存：'x'\n" * 8)
    attempts = [SqlAttempt(sql=f"SELECT {i} FROM nope_{i}", error="no such table")
                for i in range(3)]
    monkeypatch.setitem(gssc.FUSE_TOKENS, "generate", 1)  # 逼到全链走一遍
    slots = dict(gssc.gather_generate({
        "question": "有几名学生", "evidence": "", "db_schema": schema,
        "attempts": attempts, "verify_note": None, "metric_note": None,
        "session_context": {"turns": [],
                            "draft": {"sql": "SELECT " + "draft_body " * 40,
                                      "head": "标量值 3"}}}),
        examples=format_examples_block([("历", "SELECT 1")]),
        knowledge=format_knowledge_block(["贷款状态 'A' 表示正常贷款。"]))
    events = []
    out = gssc.assemble("generate", slots, on_compress=events.append)
    assert events[0]["actions"] == ["整段撤记忆", "撤值纸条", "撤字典块", "撤参考例题",
                                    "砍值采样", "缩失败历史", "缩失败历史",
                                    "缩失败历史"], \
        "逐级生效序含撤纸条与撤字典块位（摘要面后、撤参考例题前——票 05 淘汰位在册）"
    for gone in ("draft_body", VALUE_STICKER_HEADER, EXAMPLES_HEADER,
                 "贷款状态 'A' 表示正常贷款。", VALUE_SAMPLE_HEADER, "no such table"):
        assert gone not in out
    assert "CREATE TABLE a" in out and out.endswith("输出一条 SQL：")  # 地板与任务/输出面在位


def test_no_evictable_content_records_honestly(monkeypatch):
    """respond/explore 无料可砍（结果表/SQL/表清单不在淘汰序）：如实入账、不硬砍一字。"""
    monkeypatch.setitem(gssc.FUSE_TOKENS, "explore", 10)
    events = []
    out = gssc.assemble("explore", gssc.gather_schema_pick(["t1", "t2"], "问？"),
                        on_compress=events.append)
    assert out == _PICK_PROMPT.format(tables="t1, t2", question="问？")
    assert events and events[0]["actions"] == []  # 入账但零动作
    monkeypatch.setitem(gssc.FUSE_TOKENS, "respond", 10)
    events2 = []
    res = QueryResult(columns=["a"], rows=[(1,)], row_count=1, truncated=False,
                      elapsed_ms=1)
    # 敌形 SQL：含 "- " 起手行（二元减合法换行）——STATE 结构闸保证不随失败历史一起被砍
    slots = gssc.gather_respond({"question": "有几名学生"}, result=res,
                                sql="SELECT 1\n- 2\n- 3", rows_table="a\n1", n=1)
    out2 = gssc.assemble("respond", slots, on_compress=events2.append)
    assert events2 and events2[0]["actions"] == []
    assert "## 所用 SQL\nSELECT 1\n- 2\n- 3" in out2 and "## 查询结果" in out2


# ── ③ 词表硬资产：缺失＝如实炸；在位＝计数数值钉（快照确定性）──────────


def test_vocab_missing_raises_honestly(monkeypatch, tmp_path):
    monkeypatch.setattr(gssc, "VOCAB_PATH", tmp_path / "nope.tiktoken")
    monkeypatch.setattr(gssc, "_ENCODING", None)
    with pytest.raises(RuntimeError, match="词表文件缺失"):
        gssc.assemble("explore", gssc.gather_schema_pick(["t"], "问"))
    # 启动位点：load_settings 在解析任何配置前先查——词表缺失不带病启动
    with pytest.raises(RuntimeError, match="词表文件缺失"):
        load_settings()


def test_vocab_snapshot_count_pinned():
    """仓库词表＝权威快照：固定混排文本的计数数值钉（文件被换/截尾即红）；
    数字出处＝本地 tiktoken 0.14 cl100k_base 定义实测（A/B 等值已在票 04 实施期核验）。"""
    assert count_tokens("去年销售额是多少？SELECT SUM(amount) FROM t") == 16
    assert count_tokens("中英混排 mixed 123 .,;:!?（）——《》") == 18
    assert count_tokens("<|endofprompt|> 特殊记号样字样按普通文本") == 22


# ── ④ 降级入账：审计账本＋trace（票 01 出口形状）────────────────────────


def test_fuse_event_lands_ledger_and_frames(fixture_db, monkeypatch, tmp_path):
    """端到端：触发后账本有 budget_fuse 行（不计为 LLM 调用）、帧流有 tool 胶囊、
    链路照常答完（保险丝只瘦身、不吞答案）。"""
    monkeypatch.setitem(gssc.FUSE_TOKENS, "understand", 10)
    tracer = TraceLogger(tmp_path / "traces.jsonl", run_id="r1")
    frames = []
    llm = ScriptedLLM(['{"question":"查学生数"}', _GOOD, "Bob 数学 88"])
    ctx = {"turns": [{"question": "上一问的账户明细列名都是什么", "sql": _GOOD,
                      "row_count": 1, "head": "标量值 2", "failed": False}],
           "draft": None}
    ans = run_question(fixture_db, "有多少学生", llm=llm, tracer=tracer,
                       settings=_S, on_event=frames.append, session_context=ctx)
    assert ans.failed is False and llm.calls == 3
    rows = [json.loads(ln) for ln in
            (tmp_path / "traces.jsonl").read_text(encoding="utf-8").splitlines()]
    fuse_rows = [r for r in rows if r.get("node") == "budget_fuse"]
    assert fuse_rows and fuse_rows[0]["actions"] and "tokens_before" in fuse_rows[0]
    # 审计账本语义：budget_fuse 行无 token 标记＝不烧调用数（outcome 行先例）
    assert tracer.usage_for("-")["llm_calls"] == 3
    capsules = [f for f in frames
                if f.get("kind") == "tool" and f["tool"] == "budget_fuse"]
    assert capsules and capsules[0]["node"] == "understand"
    # 触发形态下任务与输出面仍在（砍的是记忆/资料料）
    assert "原始问题：有多少学生" in llm.prompts[0]
    assert "只输出一个 JSON 对象" in llm.prompts[0]


def test_fuse_capsule_mirrors_as_span(fixture_db, monkeypatch):
    """trace 面＝票 01 出口形状：胶囊经 obs 镜像成 understand 节点之子 span、带串联键。"""
    monkeypatch.setitem(gssc.FUSE_TOKENS, "understand", 10)
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    obs = Obs(provider.get_tracer("t"), "有多少学生", {"session_id": "s1"})
    llm = ScriptedLLM(['{"question":"查学生数"}', _GOOD, "Bob 数学 88"])
    run_question(fixture_db, "有多少学生", llm=llm, settings=_S, obs=obs)
    spans = exporter.get_finished_spans()
    und = next(s for s in spans if s.name == "understand")
    fuse = next(s for s in spans if s.name == "budget_fuse")
    assert fuse.parent.span_id == und.context.span_id
    assert fuse.attributes["session_id"] == "s1"


# ── ⑤ 布线钉：五挂点全带入账回调，摘线即红 ──────────────────────────────


def test_all_sites_wired_to_fuse_sink():
    import inspect
    import re

    import qadata.graph.nodes as nodes_mod
    import qadata.tools.schema as schema_mod
    nsrc, ssrc = inspect.getsource(nodes_mod), inspect.getsource(schema_mod)
    assert len(re.findall(r"on_compress=_fuse\(", nsrc)) == 4, "graph 四场景挂点"
    assert "on_compress=_on_compress" in ssrc and '"budget_fuse"' in ssrc, "explore 挂点"
    console = Path("web/src/Console.tsx").read_text(encoding="utf-8")
    assert 'budget_fuse: "预算保险丝"' in console, "前端胶囊标签在册（label 承载语义）"
