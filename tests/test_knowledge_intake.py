"""M10 票 04 专测：口径字典落盘＋进料即向量化（假 embedder 离线，零 eval 花费零联网）。

钉票面验收全表：
① 进料→落盘→逐条目向量 roundtrip——条目级切块保形（一条一块、块序＝文件序、
  块内原样），整档 embed＝一次调用（票 06「一次向量化调用＝档面本体」）；
② 装载闸——缺档 None／坏档整档如实炸／缺向量（挂账）／坏向量／manifest 缺
  model_id 全走整档报错（examples.yaml 装载闸同形）；
③ 唯一写入口钉：管理面外源码出现 feed_knowledge 进料即红（零触扫描钉族扩位——
  本钉守进料侧；消费侧两义分家归票 05）；
④ model_id 入档面 manifest：换 embedding 模型＝旧向量作废（整档 embed 天然全重刷）；
⑤ 删智能体连带清＋孤儿目录拒建；md/txt/csv 三格式切块＋Word/PDF 拒收；
  缺 embedder＝可落盘但向量化挂账明示（重喂补齐）。
隔离纪律：全程 tmp_path（票 01 评审家法，真实 data/agents 零染指）。
"""
import inspect
from pathlib import Path

import pytest
import yaml

from qadata.retrieval.knowledge import (
    INTAKE_SUFFIXES,
    KNOWLEDGE_FILENAME,
    KnowledgeError,
    feed_knowledge,
    load_knowledge,
)
from qadata.web.agents import AgentStore
from tests.fakes import BoomEmbedder, FakeEmbedder

B1 = "正常贷款：贷款状态 'A' 表示正常。"
B2 = "# 不良率口径\n逾期 90 天以上贷款余额占总贷款余额的比重。"
B3 = "发行标志：issued 字段取自表 card。"
MD_TEXT = f"{B1}\n\n{B2}\n\n"
VECS = {B1: [1.0, 0.0], B2: [0.0, 1.0], B3: [0.5, 0.5]}


@pytest.fixture
def agent_dir(tmp_path):
    d = tmp_path / "agents" / "abcd1234ef56"
    d.mkdir(parents=True)
    return d


def _src(tmp_path, name: str, content) -> Path:
    p = tmp_path / name
    if isinstance(content, bytes):
        p.write_bytes(content)
    else:
        p.write_text(content, encoding="utf-8")
    return p


def _emb() -> FakeEmbedder:
    return FakeEmbedder(VECS)


# ── ① roundtrip：切块保形·块序＝文件序·整档一批 embed ──────────────


def test_md_feed_roundtrip(agent_dir, tmp_path):
    src = _src(tmp_path, "dict.md", MD_TEXT)
    emb = _emb()
    res = feed_knowledge(agent_dir, src, emb)
    assert res == (2, 2, 2)  # total/added/embedded（NamedTuple）
    assert emb.calls == 1 and emb.batches == [[B1, B2]]  # 一次向量化调用＝档面本体
    stored = load_knowledge(agent_dir)
    assert stored.model_id == "fake-embed"
    assert [e.text for e in stored.entries] == [B1, B2]  # 块序＝文件序、块内原样（标题行随块）
    assert [list(e.vec) for e in stored.entries] == [VECS[B1], VECS[B2]]


def test_txt_chunks_like_md(agent_dir, tmp_path):
    feed_knowledge(agent_dir, _src(tmp_path, "dict.txt", MD_TEXT), _emb())
    assert [e.text for e in load_knowledge(agent_dir).entries] == [B1, B2]


def test_csv_row_per_entry(agent_dir, tmp_path):
    raw = "正常贷款,A 状态\n不良率,逾期90天以上占比\n\n发行标志,issued,\n"
    expect = ["正常贷款；A 状态", "不良率；逾期90天以上占比", "发行标志；issued"]  # 一行一条、空行弃、空胞弃
    feed_knowledge(agent_dir, _src(tmp_path, "dict.csv", raw),
                   FakeEmbedder({t: [1.0, float(i)] for i, t in enumerate(expect)}))
    assert [e.text for e in load_knowledge(agent_dir).entries] == expect


# ── 进料闸：格式白名单·同文去重·空料不建档 ────────────────────────


def test_reject_unsupported_format(agent_dir, tmp_path):
    src = _src(tmp_path, "口径.docx", b"fake")
    with pytest.raises(KnowledgeError, match="Word"):
        feed_knowledge(agent_dir, src, _emb())
    with pytest.raises(KnowledgeError, match="UTF-8"):  # 坏编码如实炸，不 errors=replace 吞
        feed_knowledge(agent_dir, _src(tmp_path, "gb.md", "坏编码".encode("gbk")), _emb())


def test_within_file_dedupe(agent_dir, tmp_path):
    """文件内同文去重＝档面 docstring「跨feed/文件内一钉」的文件内侧（重复块＝
    重复召回料，白占注入位——进料当场合并，不留到读侧打补丁）。"""
    res = feed_knowledge(agent_dir, _src(tmp_path, "d.md", f"{B1}\n\n{B1}\n\n{B2}"), _emb())
    assert res == (2, 2, 2)  # 三块切出、同文并一
    assert [e.text for e in load_knowledge(agent_dir).entries] == [B1, B2]


def test_dedupe_merge_and_whole_archive_embed(agent_dir, tmp_path):
    feed_knowledge(agent_dir, _src(tmp_path, "a.md", MD_TEXT), _emb())
    res2 = feed_knowledge(agent_dir, _src(tmp_path, "b.md", f"{B2}\n\n{B3}"), _emb())
    assert res2 == (3, 1, 3)  # 同文去重、新块追加；**整档 embed**＝三发一批（票 06 记法）
    assert [e.text for e in load_knowledge(agent_dir).entries] == [B1, B2, B3]
    res3 = feed_knowledge(agent_dir, _src(tmp_path, "a.md", MD_TEXT), _emb())
    assert res3.added == 0  # 重喂同文件零新增（幂等，不重复条目；整档 embed 照刷）


def test_empty_intake_rejected_and_archive_untouched(agent_dir, tmp_path):
    """空进料（0 块）＝如实拒绝——不白刷整档 embed、更不动已见账的档。"""
    d = tmp_path / "ag"
    d.mkdir()
    with pytest.raises(KnowledgeError, match="0 条目"):
        feed_knowledge(d, _src(tmp_path, "e.md", "  \n\n  "), _emb())
    assert not (d / KNOWLEDGE_FILENAME).exists()
    feed_knowledge(agent_dir, _src(tmp_path, "a.md", MD_TEXT), _emb())
    before = (agent_dir / KNOWLEDGE_FILENAME).read_bytes()
    with pytest.raises(KnowledgeError, match="0 条目"):
        feed_knowledge(agent_dir, _src(tmp_path, "e.md", "\n \n"), _emb())
    assert (agent_dir / KNOWLEDGE_FILENAME).read_bytes() == before


def test_endpoint_down_leaves_archive_intact(agent_dir, tmp_path):
    """「embed 先于写、端点炸旧档原样不动」的承重断言配钉（BoomEmbedder 先例）。"""
    feed_knowledge(agent_dir, _src(tmp_path, "a.md", MD_TEXT), _emb())
    before = (agent_dir / KNOWLEDGE_FILENAME).read_bytes()
    with pytest.raises(ValueError, match="炸"):
        feed_knowledge(agent_dir, _src(tmp_path, "b.md", B3), BoomEmbedder())
    assert (agent_dir / KNOWLEDGE_FILENAME).read_bytes() == before
    ghost = tmp_path / "fresh"
    ghost.mkdir()
    with pytest.raises(ValueError, match="炸"):
        feed_knowledge(ghost, _src(tmp_path, "b.md", B3), BoomEmbedder())
    assert not (ghost / KNOWLEDGE_FILENAME).exists()  # 首料端点挂＝不落半档


# ── ② 装载闸（examples 同形）──────────────────────────────────────


def test_load_gates(agent_dir, tmp_path):
    assert load_knowledge(agent_dir) is None  # 缺档＝None＝默认关
    f = agent_dir / KNOWLEDGE_FILENAME
    f.write_text("manifest: {model_id: m", encoding="utf-8")  # 不闭合 flow mapping
    with pytest.raises(KnowledgeError, match="读不动"):
        load_knowledge(agent_dir)
    f.write_text("[]", encoding="utf-8")
    with pytest.raises(KnowledgeError, match="形状不正"):
        load_knowledge(agent_dir)
    f.write_text(yaml.safe_dump(
        {"manifest": {"model_id": "m"}, "entries": [{"vec": [1.0]}]},
        allow_unicode=True), encoding="utf-8")
    with pytest.raises(KnowledgeError, match="text 须非空"):
        load_knowledge(agent_dir)
    f.write_text(yaml.safe_dump(
        {"manifest": {"model_id": "m"},
         "entries": [{"text": B1, "vec": ["x"]}]}, allow_unicode=True), encoding="utf-8")
    with pytest.raises(KnowledgeError, match="向量列不正"):
        load_knowledge(agent_dir)
    f.write_text(yaml.safe_dump(  # 有向量却缺 model_id＝自相矛盾档，宁炸不带病
        {"manifest": {}, "entries": [{"text": B1, "vec": [1.0]}]},
        allow_unicode=True), encoding="utf-8")
    with pytest.raises(KnowledgeError, match="model_id"):
        load_knowledge(agent_dir)
    f.write_text("", encoding="utf-8")
    stored = load_knowledge(agent_dir)  # 空文件＝空池宽容（examples 同款）
    assert stored is not None and stored.entries == ()


def test_pending_without_embedder_then_complete(agent_dir, tmp_path):
    src = _src(tmp_path, "dict.md", MD_TEXT)
    res = feed_knowledge(agent_dir, src, None)  # 缺 embedder＝可落盘
    assert res.embedded == 0 and res.total == 2
    data = yaml.safe_load((agent_dir / KNOWLEDGE_FILENAME).read_text(encoding="utf-8"))
    assert data["manifest"]["model_id"] == "" and all("vec" not in e for e in data["entries"])
    with pytest.raises(KnowledgeError, match="挂账"):  # 挂账明示，不静默放行半残料
        load_knowledge(agent_dir)
    feed_knowledge(agent_dir, src, _emb())  # 重喂补齐
    assert all(e.vec for e in load_knowledge(agent_dir).entries)


# ── ④ 换模型作废 ──────────────────────────────────────────────────


def test_model_change_invalidates(agent_dir, tmp_path):
    feed_knowledge(agent_dir, _src(tmp_path, "dict.md", MD_TEXT), _emb())
    v2 = FakeEmbedder({B1: [9.0], B2: [8.0], B3: [7.0]})
    v2.model = "fake-v2"
    feed_knowledge(agent_dir, _src(tmp_path, "dict.md", MD_TEXT), v2)  # 同料重喂
    stored = load_knowledge(agent_dir)
    assert stored.model_id == "fake-v2"  # manifest 更新（读侧过期判定料，票 05 消费）
    assert [list(e.vec) for e in stored.entries] == [[9.0], [8.0]]  # 旧向量整档作废


# ── ⑤ 孤儿拒建·删智能体连带清·③ 唯一写入口零触钉·常量钉 ──────────


def test_orphan_dir_and_agent_delete_cascade(tmp_path):
    with pytest.raises(KnowledgeError, match="孤儿"):
        feed_knowledge(tmp_path / "ghost", _src(tmp_path, "d.md", MD_TEXT), _emb())
    store = AgentStore(tmp_path / "agents")
    meta = store.create("口径测试")
    d = store.agent_dir(meta.id)
    feed_knowledge(d, _src(tmp_path, "d.md", MD_TEXT), _emb())
    assert (d / KNOWLEDGE_FILENAME).is_file()
    store.delete(meta.id)  # 人进料域＝目录连带清（ADR-0004，无悬挂第二真源）
    assert not d.exists()


def test_intake_is_management_only():
    """进料调用出现在管理面（CLI knowledge-feed）之外即红——问数路径永不写档。
    本钉守**进料侧**（票 04 范围）；消费侧接读归票 05，届时两义分家照例题/值档先例
    改写本钉禁列，勿扩用成挡查询路（spec §五）。"""
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
                eval_bird, sessions_mod, feedback_mod):  # 覆盖面沿 M10 零触先例全表
        assert "feed_knowledge" not in inspect.getsource(mod), mod.__name__


def test_constants():
    assert KNOWLEDGE_FILENAME == "knowledge.yaml"
    assert INTAKE_SUFFIXES == frozenset({".md", ".txt", ".csv"})
