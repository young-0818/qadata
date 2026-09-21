"""M10 票 03 专测：值链查询＋贴纸条（intent 搭车·top-K 相对边际，离线零联网零 eval 花费）。

钉票面验收全表：
① 跨文字体系合成钉——探针语料（北京分行→'BJ Branch'，ticket00）在假 embedder 下
  纸条成形；同文精确命中＝免费保底轴，无条件进且排最前（列与值两面）；
② 两闸数值钉——逐列相对边际（≥0.9×列内最高分，CHESS 先例）＋绝对阈值第二道闸
  （探针正误 margin 仅 0.043，绝对阈值只作二道闸）；逐列 top-K 截尾；
③ 四路降级各一钉（intent=None／无关键词料／缺值档／缺 embedder）＋坏档/过期/端点挂
  三路（recall 行先例形制）——账本 value_link 行无 token 标记＝不烧生成调用数；
④ 每问 ≤1 次向量调用（题面 memo 重试不翻倍）；入选表过滤（未选表的命中不贴）；
⑤ 端到端——纸条在场＝schema 上下文最末唯一插块（任务/输出/角色分区逐字节零动）、
  无命中/未挂接＝generate prompt 逐字节现状姊妹钉、零新增生成调用；
⑥ 挂接面末位参纪律钉（make_nodes/build_graph/run_question/resume_question 沿族、
  不进状态键）＋serve 播报接读值档（票 02 遗留兑现，坏/过期点名、缺/ok 静）＋常量钉。
隔离纪律：全程 root=tmp_path（票 01 评审家法）。保险丝撤纸条扩钉在 test_budget_fuse。
"""
import inspect
import json
from pathlib import Path

import pytest

from qadata.config import Settings
from qadata.graph.build import run_question
from qadata.graph.state import AgentState
from qadata.llm.tracing import TraceLogger
from qadata.retrieval.store import VALUE_FILENAME, index_dir_for, write_values
from qadata.retrieval.values import (
    VALUE_LINK_MARGIN,
    VALUE_LINK_MIN_SCORE,
    VALUE_LINK_TOP_K,
    VALUE_STICKER_HEADER,
    build_value_link,
    extract_keywords,
)
from qadata.tools.db import open_readonly
from qadata.tools.schema import build_schema_context
from qadata.web.agents import AgentStore
from qadata.web.serve import index_announcements
from tests.fakes import BoomEmbedder, FakeEmbedder, ScriptedLLM

_S = Settings(api_key="", base_url="", model="test-model")


@pytest.fixture
def ix(tmp_path):
    return tmp_path / "indexes"


def _unit(cos: float) -> list[float]:
    """二维单位向量 [cos, sin]——与查询向 [1,0] 的余弦＝cos（假 embedder 复刻探针形制）。"""
    return [cos, (1.0 - cos * cos) ** 0.5]


def _db(tmp_path, name="v.sqlite") -> str:
    """假库占位（值链查询不碰库本体——manifest 哈希要真文件，touch 即足）。"""
    p = tmp_path / name
    p.touch()
    return str(p)


def _rows(tracer_path: Path) -> list[dict]:
    return [json.loads(ln) for ln in
            tracer_path.read_text(encoding="utf-8").splitlines()]


def _vl_rows(trace_path: Path) -> list[dict]:
    return [x for x in _rows(trace_path) if x["node"] == "value_link"]


@pytest.fixture
def fresh(tmp_path, ix):
    """一库一新鲜值档（model_id=fake-embed，列 t.c 单值 hit 与查询向 [1,0] 同轴）。"""
    db = _db(tmp_path, "d.sqlite")
    write_values(db, "fake-embed", [("t", "c", ["hit"])], {"hit": [1.0, 0.0]}, root=ix)
    return db, tmp_path / "t.jsonl", ix


# ── ① 跨文字体系合成钉＋精确命中保底轴 ─────────────────────────────────


def test_probe_corpus_sticker_forms(tmp_path, ix):
    """票 00 探针语料复刻（北京分行→BJ Branch 0.578 居首、Prague 0.535、正常贷款
    0.288 出局）：假 embedder 下纸条成形＝跨文链上的机制本体。"""
    db = _db(tmp_path)
    vec = {"BJ Branch": _unit(0.578), "Prague - branch": _unit(0.535),
           "正常贷款": _unit(0.288)}
    write_values(db, "fake-embed", [("branch", "nm", list(vec))], vec, root=ix)
    emb = FakeEmbedder({"北京分行": [1.0, 0.0]})
    link = build_value_link(db, emb, root=ix)
    block = link("北京分行", {"filters": ["北京分行"]}, ["branch"])
    assert block.startswith(VALUE_STICKER_HEADER)
    assert "库里实际这么存：'BJ Branch'" in block
    assert "正常贷款" not in block  # 0.288 双闸皆出局
    assert "Prague - branch" in block  # 0.535≥0.9×0.578——margin 薄的探针实感在册（票 07）
    assert emb.calls == 1 and emb.batches == [["北京分行"]]  # 一批一发＝每问唯一向量调用


def test_exact_hit_floor_and_first(tmp_path, ix):
    """同文精确命中＝免费保底轴：向量分再差（正交＝0 分）也进、且排在该列最前与
    全块最前（票 06 hybrid 字典序先例——不治跨文、同文必靠前）。"""
    db = _db(tmp_path)
    write_values(db, "fake-embed",
                 [("a", "x", ["gold", "shiny"]), ("b", "y", ["bright"])],
                 {"gold": [1.0, 0.0], "shiny": [0.0, 1.0], "bright": [0.0, 1.0]}, root=ix)
    emb = FakeEmbedder({"gold": [0.0, 1.0]})  # 与 gold 正交＝向量面完全漏
    link = build_value_link(db, emb, root=ix)
    block = link("gold", {"filters": ["gold"]}, ["a", "b"])
    lines = [ln for ln in block.splitlines() if ln.startswith("- ")]
    assert len(lines) == 2
    assert lines[0].startswith("- a.x")  # 含精确命中的列靠前（b.y 纯向量列在后）
    assert lines[0].index("'gold'") < lines[0].index("'shiny'")


# ── ② 两闸数值钉＋逐列 top-K ──────────────────────────────────────────


def test_relative_margin_and_absolute_floor(tmp_path, ix):
    """margin 线切高过绝对闸但离列冠远者（0.6/0.52＜0.85×0.9）；整列最高分＜绝对
    阈＝全列不贴（第二道闸兜"列冠本身就不像"）。数值＝票 07 定标后（k5/m0.85/s0.35）。"""
    db = _db(tmp_path)
    vec = {"hi": _unit(0.9), "mid": _unit(0.6), "n052": _unit(0.52), "weak": _unit(0.3)}
    write_values(db, "fake-embed", [("c", "v", ["hi", "mid", "n052"]),
                                    ("d", "v", ["weak"])], vec, root=ix)
    emb = FakeEmbedder({"kw": [1.0, 0.0]})
    block = build_value_link(db, emb, root=ix)("kw", {"filters": ["kw"]}, ["c", "d"])
    assert "'hi'" in block and "'mid'" not in block and "'n052'" not in block
    assert "d.v" not in block  # 列冠 0.3＜0.35 绝对闸＝整列出局（相对边际无参照不贴）


def test_top_k_per_column(tmp_path, ix):
    """逐列 top-K 截尾：同列六条全过线也只贴前五（分数降序；K＝票 07 定标值 5）。"""
    db = _db(tmp_path)
    vals = {"v95": 0.95, "v94": 0.94, "v93": 0.93, "v92": 0.92, "v91": 0.91, "v90": 0.90}
    write_values(db, "fake-embed", [("t", "c", list(vals))],
                 {k: _unit(c) for k, c in vals.items()}, root=ix)
    emb = FakeEmbedder({"kw": [1.0, 0.0]})
    block = build_value_link(db, emb, root=ix)("kw", {"filters": ["kw"]}, ["t"])
    assert "'v95'" in block and "'v91'" in block and "'v90'" not in block
    assert block.index("v95") < block.index("v94") < block.index("v93")


def test_filter_by_selected_tables(tmp_path, ix):
    """纸条只贴最终入选表的命中列（票面「相关列旁挂」）；未选＝""＝逐字节现状。"""
    db = _db(tmp_path)
    write_values(db, "fake-embed", [("picked", "c", ["ab"])], {"ab": [1.0, 0.0]}, root=ix)
    emb = FakeEmbedder({"ab": [1.0, 0.0]})
    link = build_value_link(db, emb, root=ix)
    assert link("ab", {"filters": ["ab"]}, ["other"]) == ""
    assert "picked.c" in link("ab", {"filters": ["ab"]}, ["picked"])
    assert emb.calls == 1  # 扫描按题面 memo，渲染随表集现算不重 embed


# ── ③ 四路降级＋坏档/过期/端点挂（账本形制） ──────────────────────────


def test_intent_none_skips_without_vectorize(fresh):
    """搭车载体缺席＝本轮无纸条（spec §二 Q5 成文），连题面抽词都不发——零向量调用。"""
    db, tp, ix = fresh
    emb = FakeEmbedder({})
    link = build_value_link(db, emb, tracer=TraceLogger(tp, run_id="r"), root=ix)
    assert link("有学生", None, ["t"]) == ""
    row = _vl_rows(tp)[0]
    assert row["outcome"] == "skipped" and "intent=None" in row["reason"]
    assert emb.calls == 0 and "input_tokens" not in row


def test_no_keyword_material_skips(fresh):
    db, tp, ix = fresh
    emb = FakeEmbedder({})
    link = build_value_link(db, emb, tracer=TraceLogger(tp, run_id="r"), root=ix)
    assert link("？！", {"filters": []}, ["t"]) == ""
    row = _vl_rows(tp)[0]
    assert row["outcome"] == "skipped" and "无关键词料" in row["reason"]
    assert emb.calls == 0


def test_missing_archive_skips(tmp_path, ix):
    """缺值档＝默认关，但票 03 起入账可见（skipped 行给出可操作指令）；
    空档（建过无可采列）两账分文——serve 判 ok/静默，查询面说「值档空」不谎称缺。"""
    tp = tmp_path / "t.jsonl"
    emb = FakeEmbedder({})
    link = build_value_link(str(tmp_path / "none.sqlite"), emb,
                            tracer=TraceLogger(tp, run_id="r"), root=ix)
    assert link("有学生", {"filters": ["有学生"]}, ["t"]) == ""
    row = _vl_rows(tp)[0]
    assert row["outcome"] == "skipped" and "缺值索引档" in row["reason"]
    assert emb.calls == 0 and "index-build" in row["reason"]
    empty_db = _db(tmp_path, "empty.sqlite")
    write_values(empty_db, "fake-embed", [], {}, root=ix)
    link2 = build_value_link(empty_db, emb, tracer=TraceLogger(tp, run_id="r"), root=ix)
    assert link2("有学生", {"filters": ["有学生"]}, ["t"]) == ""
    assert "值档空" in _vl_rows(tp)[-1]["reason"]
    assert "缺" not in _vl_rows(tp)[-1]["reason"]  # 评审追补：文案与 serve 态不互相打脸


def test_missing_embedder_logged_when_index_exists(fresh):
    """缺 embedder 只在有档可建链时点名（账目归因：缺档闸在前，一行不套双份）。"""
    db, tp, ix = fresh
    link = build_value_link(db, None, tracer=TraceLogger(tp, run_id="r"), root=ix)
    assert link("有学生", {"filters": ["有学生"]}, ["t"]) == ""
    row = _vl_rows(tp)[0]
    assert row["outcome"] == "failed"
    assert row["reason"] == "向量化未配置（QADATA_EMBED_MODEL 为空）"
    assert row["pool"] == 1


def test_broken_stale_boom_all_degrade(tmp_path, ix):
    """坏档/过期/端点挂三路（recall 先例）：入账、照常、不静默，维度不合也不外抛。"""
    db = _db(tmp_path, "d.sqlite")
    tp = tmp_path / "t.jsonl"
    f = index_dir_for(db, root=ix)
    f.mkdir(parents=True)
    (f / VALUE_FILENAME).write_text("这不是字典", encoding="utf-8")
    emb = FakeEmbedder({"有学生": [1.0]})
    build_value_link(db, emb, tracer=TraceLogger(tp, run_id="r"), root=ix)(
        "有学生", {"filters": ["有学生"]}, ["t"])
    assert _vl_rows(tp)[-1]["reason"].startswith("值索引档")  # 前缀单源不套双份（examples 纪律）
    write_values(db, "old-model", [("t", "c", ["hit"])], {"hit": [1.0]}, root=ix)
    build_value_link(db, emb, tracer=TraceLogger(tp, run_id="r"), root=ix)(
        "有学生", {"filters": ["有学生"]}, ["t"])
    assert _vl_rows(tp)[-1]["outcome"] == "failed" and "过期" in _vl_rows(tp)[-1]["reason"]
    write_values(db, "boom-embed", [("t", "c", ["hit"])], {"hit": [1.0]}, root=ix)
    build_value_link(db, BoomEmbedder(), tracer=TraceLogger(tp, run_id="r"), root=ix)(
        "有学生", {"filters": ["有学生"]}, ["t"])
    last = _vl_rows(tp)[-1]
    assert last["outcome"] == "failed" and "向量化调用失败" in last["reason"]
    assert all("input_tokens" not in r for r in _vl_rows(tp))


# ── ④ memo 与账本 ok 行形制 ───────────────────────────────────────────


def test_memo_and_ok_row_shape(fresh):
    db, tp, ix = fresh
    emb = FakeEmbedder({"有学生": [1.0, 0.0], "另一个问题": [0.0, 1.0]})
    link = build_value_link(db, emb, tracer=TraceLogger(tp, run_id="r"), root=ix)
    first = link("有学生", {"filters": []}, ["t"])
    assert first and link("有学生", {"filters": []}, ["t"]) == first
    assert link("另一个问题", {"filters": []}, ["t"]) == ""  # cos 0＝双闸出局（hits=0 仍 ok 行）
    assert emb.batches == [["有学生"], ["另一个问题"]]  # 每问一发、同问 memo 不翻倍
    ok = [r for r in _vl_rows(tp) if r["outcome"] == "ok"]
    assert len(ok) == 2 and ok[0]["hits"] == 1 and ok[0]["pool"] == 1
    assert ok[0]["kws"] == 1 and ok[0]["model"] == "fake-embed"
    assert ok[1]["hits"] == 0
    assert "latency_s" in ok[0] and "input_tokens" not in ok[0]


# ── ⑤ 端到端：纸条唯一插块／无命中逐字节现状姊妹钉 ─────────────────────


_WITH_FILTER = '{"question":"查学生数","intent":{"filters":["math"]}}'
_NO_FILTER = '{"question":"查学生数","intent":{}}'  # 载体在、filters 空＝料只从题面 CJK 连串来


def _happy(script=None):
    return ScriptedLLM(script or [_WITH_FILTER, "SELECT COUNT(*) FROM students", "共 2 人"])


def _plain_ctx(db):
    conn = open_readonly(db)
    try:
        return build_schema_context(conn, "查学生数")
    finally:
        conn.close()


def test_e2e_sticker_is_the_only_delta(fixture_db, ix):
    """纸条在场＝schema 上下文末尾唯一插块（任务/输出/角色分区逐字节零动——
    A 与无检索基线只差 `"\\n\\n"+block` 一截），零新增生成调用、每问一发向量。"""
    write_values(fixture_db, "fake-embed",
                 [("scores", "subject", ["math", "english"])],
                 {"math": [1.0, 0.0], "english": _unit(0.7071)}, root=ix)
    emb = FakeEmbedder({"math": [1.0, 0.0], "查学生数": [0.0, 1.0]})
    llm_a, llm_c = _happy(), _happy()
    run_question(fixture_db, "有多少学生", llm=llm_a, settings=_S,
                 value_link=build_value_link(fixture_db, emb, root=ix))
    run_question(fixture_db, "有多少学生", llm=llm_c, settings=_S)
    assert llm_a.calls == 3 and llm_c.calls == 3  # 值链零新增生成调用
    gp_a, gp_c = llm_a.prompts[1], llm_c.prompts[1]
    ctx = _plain_ctx(fixture_db)
    block = VALUE_STICKER_HEADER + "\n- scores.subject —— 库里实际这么存：'math'"
    assert gp_a == gp_c.replace(ctx, ctx + "\n\n" + block, 1)  # 唯一差异＝ctx 末尾贴块
    assert gp_a.endswith("输出一条 SQL：")  # 输出分区永不位移
    assert emb.batches == [["math", "查学生数"]]  # 搭车 filters＋题面 CJK 连串＝一批一发
    # english（cos 0.707）被列冠 1.0 的相对线切掉＝窄出后仍按入选表贴（scores 在选中集）


def test_e2e_no_hits_byte_identical(fixture_db, ix):
    """无命中（有档有 embedder、扫了没贴）＝generate prompt 与未挂接逐字节现状姊妹钉。"""
    write_values(fixture_db, "fake-embed", [("students", "name", ["zzz"])],
                 {"zzz": [1.0, 0.0]}, root=ix)
    emb = FakeEmbedder({"查学生数": [0.0, 1.0]})
    script = [_NO_FILTER, "SELECT COUNT(*) FROM students", "共 2 人"]
    llm_b, llm_c = _happy(list(script)), _happy(list(script))
    run_question(fixture_db, "有多少学生", llm=llm_b, settings=_S,
                 value_link=build_value_link(fixture_db, emb, root=ix))
    run_question(fixture_db, "有多少学生", llm=llm_c, settings=_S)
    assert llm_b.prompts == llm_c.prompts
    assert emb.batches == [["查学生数"]]


def test_e2e_intent_none_zero_vector_and_current(fixture_db, tmp_path, ix):
    """解析失败回退态（understand 出纯文本）＝零向量调用＋prompt 逐字节现状——
    「检索任何一路缺＝降级现状路径」在图面的端到端背书。"""
    write_values(fixture_db, "fake-embed", [("students", "name", ["zz"])],
                 {"zz": [1.0]}, root=ix)
    emb = FakeEmbedder({})
    trace_path = tmp_path / "t.jsonl"
    tracer = TraceLogger(trace_path, run_id="r")
    plain = ["查学生数", "SELECT COUNT(*) FROM students", "共 2 人"]
    llm_b, llm_c = _happy(plain), _happy(plain)
    run_question(fixture_db, "有多少学生", llm=llm_b, settings=_S, tracer=tracer,
                 value_link=build_value_link(fixture_db, emb, tracer=tracer, root=ix))
    run_question(fixture_db, "有多少学生", llm=llm_c, settings=_S)
    assert llm_b.prompts == llm_c.prompts and emb.calls == 0
    assert _vl_rows(trace_path)[0]["outcome"] == "skipped"
    assert tracer.usage_for("-")["llm_calls"] == 3  # 检索行不掺生成调用数（recall 先例）


# ── 关键词面（共享单源）＋常量＋布线钉 ────────────────────────────────


def test_extract_keywords_cjk_flag():
    """值链旗开＝CJK 连续串不分词（"北京分行"整串）；例题面缺省关＝行为不动。"""
    text = "北京分行 A03 去年 'gold'"
    assert extract_keywords(text, cjk=True) == {"a03", "gold", "北京分行", "去年"}
    assert extract_keywords(text) == {"a03", "gold"}


def test_constants_pinned():
    assert VALUE_LINK_TOP_K == 5
    assert VALUE_LINK_MARGIN == 0.85   # 票 07 定标（免费网格：0.9 把列内正解切掉，实拍）
    assert VALUE_LINK_MIN_SCORE == 0.35  # 绝对二道闸——票 07 定标（0.5 整段误杀跨文带）
    assert VALUE_STICKER_HEADER.startswith("值纸条")


def test_calibration_note_registered_in_source():
    """CJK 边际线已定标＋定标注记在册（票面验收第四条「定标后旋钮值回写为测钉」的机制钉）。"""
    import qadata.retrieval.values as v
    src = inspect.getsource(v)
    assert src.count("票 07 定标") >= 2
    assert "票 07 已定标" in src  # 判读锚（保守值不达线→定标值的证据链在注释里）


def test_run_question_tail_param_value_link():
    # build_graph/resume_question 尾四位钉在 test_table_cards（更新原钉）——此处只补
    # run_question 门面形（评审追补：勿三处逐字复制同一断言）
    params = list(inspect.signature(run_question).parameters)
    assert params[-4:] == ["recall", "table_recall", "value_link", "knowledge_recall"]
    assert "value_link" not in AgentState.__annotations__  # 不进状态键（可调用沿参纪律）


def test_eval_wired_cli_not():
    """库派生物验收必须经 eval（spec §五 两义分家）：eval 装配点在册；CLI ask 不挂＝现状
    （表卡裁决同族，test_embedding_recall 的 cli "recall" 零挂钉继续守）。"""
    import qadata.cli.main as cli_main
    import qadata.eval.bird as eval_bird

    assert "build_value_link" in inspect.getsource(eval_bird)
    assert "build_value_link" not in inspect.getsource(cli_main)


# ── serve 播报接读值档（票 02 遗留兑现） ──────────────────────────────


def test_serve_announcement_reads_value_index(tmp_path, fixture_db, ix):
    store = AgentStore(tmp_path / "agents")
    a = store.create("学校")
    p = store.store_datasource(a.id, Path(fixture_db).read_bytes(), "school.sqlite")
    vf = index_dir_for(p, root=ix) / VALUE_FILENAME
    assert "值索引" not in "\n".join(index_announcements(store, "fake-embed", root=ix))  # 缺＝静
    write_values(p, "fake-embed", [("t", "c", ["x"])], {"x": [1.0]}, root=ix)
    assert "值索引" not in "\n".join(index_announcements(store, "fake-embed", root=ix))  # ok＝静
    vf.write_text("这不是字典", encoding="utf-8")
    assert "值索引坏档" in "\n".join(index_announcements(store, "fake-embed", root=ix))
    write_values(p, "old-model", [("t", "c", ["x"])], {"x": [1.0]}, root=ix)
    assert "值索引过期" in "\n".join(index_announcements(store, "fake-embed", root=ix))
