"""M10 票 04 专测：口径字典落盘＋进料即向量化（假 embedder 离线，零 eval 花费零联网）。

钉票面验收全表：
① 进料→落盘→逐条目向量 roundtrip——条目级切块保形（一条一块、块序＝文件序、
  块内原样），整档 embed＝一次调用（票 06「一次向量化调用＝档面本体」）；
② 装载闸——缺档 None／坏档整档如实炸／缺向量（挂账）／坏向量／manifest 缺
  model_id 全走整档报错（examples.yaml 装载闸同形）；
③ 问数路径零进料钉（写入口单源 feed_knowledge；门＝CLI knowledge-feed＋
  （owner 裁 2026-09-21 web 化前提）web 进料端点，双门共口、禁列见钉内注记）；
④ model_id 入档面 manifest：换 embedding 模型＝旧向量作废（整档 embed 天然全重刷）；
⑤ 删智能体连带清＋孤儿目录拒建；md/txt/csv 三格式切块＋Word/PDF 拒收；
  缺 embedder＝可落盘但向量化挂账明示（重喂补齐）。
隔离纪律：全程 tmp_path（票 01 评审家法，真实 data/agents 零染指）。
"""
import inspect
import tempfile
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from qadata.config import Settings
from qadata.retrieval.knowledge import (
    INTAKE_SUFFIXES,
    KNOWLEDGE_FILENAME,
    PENDING_EMBED_NOTE,
    KnowledgeError,
    feed_knowledge,
    load_knowledge,
)
from qadata.web.agents import AgentStore
from qadata.web.app import create_app
from tests.fakes import BoomEmbedder, FakeEmbedder, ScriptedLLM

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
    """进料调用出现在问数路径即红——查询路永不写档。
    本钉守**进料侧**（票 04 范围）；禁列随 owner 裁 2026-09-21「web 化前提」收窄：
    web/app 追加进料门（POST /api/agents/{id}/knowledge 与 CLI 双门共写入口
    feed_knowledge——写门二、写口一，本钉本意「问数路径永不进料」不受影响），
    app 端点行为测在 test_web_intake_* 三钉。"""
    import qadata.eval.bird as eval_bird
    import qadata.graph.build as build_mod
    import qadata.graph.gssc as gssc_mod
    import qadata.graph.nodes as nodes_mod
    import qadata.tools.schema as schema_mod
    import qadata.web.feedback as feedback_mod
    import qadata.web.serve as serve_mod
    import qadata.web.sessions as sessions_mod

    for mod in (build_mod, nodes_mod, gssc_mod, schema_mod, serve_mod,
                eval_bird, sessions_mod, feedback_mod):  # 覆盖面沿 M10 零触先例全表
        assert "feed_knowledge" not in inspect.getsource(mod), mod.__name__


# ── 进料 web 门（owner 裁 2026-09-21 web 化前提；双门共写入口）──────────


def _web_client(tmp_path, embedder):
    store = AgentStore(tmp_path / "agents")
    meta = store.create("字典门")
    client = TestClient(create_app(
        llm=ScriptedLLM([]), settings=Settings(api_key="", base_url="", model="m"),
        agents=store, static_dir="__no_such_dist_for_tests__", embedder=embedder))
    return store, meta, client


def test_web_intake_roundtrip(tmp_path, monkeypatch):
    """web 门进料＝与 CLI 同语义（合并去重、只增不删；回执整档/新增/向量化）。"""
    made = []
    real_mkstemp = tempfile.mkstemp

    def spy(**kw):  # 临时文件留尸钉：记录端点造过的每个 tmp，请求收口后必须全部不在
        fd, name = real_mkstemp(**kw)
        made.append(name)
        return fd, name

    monkeypatch.setattr(tempfile, "mkstemp", spy)
    store, meta, client = _web_client(tmp_path, _emb())
    r = client.post(f"/api/agents/{meta.id}/knowledge", params={"name": "d.md"},
                    content=MD_TEXT.encode("utf-8"))
    assert r.status_code == 200
    assert r.json() == {"total": 2, "added": 2, "embedded": 2, "note": ""}
    assert [e.text for e in load_knowledge(store.agent_dir(meta.id)).entries] == [B1, B2]
    r2 = client.post(f"/api/agents/{meta.id}/knowledge", params={"name": "d.md"},
                     content=MD_TEXT.encode("utf-8"))
    assert r2.json()["added"] == 0  # 重喂幂等（档面纪律在端点面的透传）
    assert made and all(not Path(n).exists() for n in made)  # finally 清尸（含失败路）


def test_web_intake_rejections_human_words(tmp_path):
    """一切拒绝转人话（CLI 薄壳同形）：坏格式 400 指路、空料 400、无此智能体 404。"""
    store, meta, client = _web_client(tmp_path, _emb())
    r = client.post(f"/api/agents/{meta.id}/knowledge", params={"name": "x.docx"},
                    content=b"fake")
    assert r.status_code == 400 and "Word" in r.json()["detail"]
    assert not (store.agent_dir(meta.id) / KNOWLEDGE_FILENAME).exists()  # 拒在落盘前
    r = client.post(f"/api/agents/{meta.id}/knowledge", params={"name": "e.md"},
                    content=b"  \n\n  ")
    assert r.status_code == 400 and "0 条目" in r.json()["detail"]
    r = client.post("/api/agents/000000000000/knowledge", params={"name": "d.md"},
                    content=MD_TEXT.encode("utf-8"))
    assert r.status_code == 404


def test_web_intake_pending_note(tmp_path):
    """缺 embedder＝可落盘、回执 note 回挂账真话——文案与 CLI 黄字共读单源
    （PENDING_EMBED_NOTE 双消费者，字面漂移即红）。"""
    import qadata.cli.main as cli_main

    store, meta, client = _web_client(tmp_path, None)
    r = client.post(f"/api/agents/{meta.id}/knowledge", params={"name": "d.md"},
                    content=MD_TEXT.encode("utf-8"))
    body = r.json()
    assert body["embedded"] == 0 and body["note"] == PENDING_EMBED_NOTE
    assert (store.agent_dir(meta.id) / KNOWLEDGE_FILENAME).is_file()  # 内容不丢
    with pytest.raises(KnowledgeError, match="挂账"):  # 装载闸照旧拒读
        load_knowledge(store.agent_dir(meta.id))
    assert "PENDING_EMBED_NOTE" in inspect.getsource(cli_main)  # CLI 共读在册


def test_constants():
    assert KNOWLEDGE_FILENAME == "knowledge.yaml"
    assert INTAKE_SUFFIXES == frozenset({".md", ".txt", ".csv"})
