"""M11 票 02 建索引 web 门：契约测试（纯离线、tmp 索引目录、零触真实 data/indexes）。

钉：闸三件（无 embedder 400／无数据源 400／查无智能体 404）、后台构建到终态＋详情
读数转正（cards/values＝ok）、在途 409、ADR-0005 零触扫描（问数路径源码出现
build_index/build_value_index 即红——端点与 CLI 是仅有的两把门）。
"""
import inspect
import threading
import time
import types

import pytest
from fastapi.testclient import TestClient

from qadata.config import Settings
from qadata.eval import bird as eval_bird
from qadata.graph import build as graph_build
from qadata.graph import nodes as graph_nodes
from qadata.retrieval import cards
from qadata.web import sessions as web_sessions
from qadata.web.agents import AgentStore
from qadata.web.app import create_app

_S = Settings(api_key="", base_url="", model="test-model")


class AnyEmbedder:
    """确定性任意文本假通道：本文件测布线不测向量质量（质量归
    test_table_cards/test_value_index；FakeEmbedder 的「未脚本即错」纪律在
    构建面不合身——卡/值文本集合大且由 schema 派生）。"""

    model = "fake-embed"

    def embed(self, texts):
        return [[float(len(t) % 7 + 1), float(i % 5), 1.0] for i, t in enumerate(texts)]


@pytest.fixture
def store(tmp_path):
    return AgentStore(tmp_path / "agents")


def _client(store, tmp_path, embedder=None):
    return TestClient(create_app(llm=None, settings=_S, agents=store,
                                 static_dir="__no_such_dist_for_tests__",
                                 embedder=embedder, index_root=tmp_path / "idx"))


def _agent_with_db(store, fixture_db):
    a = store.create("试验智能体", "建索引 web 门测试")
    with open(fixture_db, "rb") as f:
        store.store_datasource(a.id, f.read(), "school.sqlite")
    return a


def _wait_job(client, agent_id, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/agents/{agent_id}").json()["index"]["job"]
        if job and job["state"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("索引构建 job 未在时限内到终态")


# ── 闸三件 ──────────────────────────────────────────────────────────

def test_gate_no_embedder_400(store, tmp_path, fixture_db):
    a = _agent_with_db(store, fixture_db)
    res = _client(store, tmp_path, None).post(f"/api/agents/{a.id}/index-build")
    assert res.status_code == 400 and "QADATA_EMBED_MODEL" in res.json()["detail"]


def test_gate_no_datasource_400(store, tmp_path):
    a = store.create("空智能体", "无库")
    res = _client(store, tmp_path, AnyEmbedder()).post(f"/api/agents/{a.id}/index-build")
    assert res.status_code == 400 and "数据源" in res.json()["detail"]


def test_gate_unknown_agent_404(store, tmp_path):
    res = _client(store, tmp_path, AnyEmbedder()).post("/api/agents/deadbeefcafe/index-build")
    assert res.status_code == 404


# ── 构建到终态＋读数转正 ────────────────────────────────────────────

def test_build_completes_and_detail_flips_ok(store, tmp_path, fixture_db):
    a = _agent_with_db(store, fixture_db)
    client = _client(store, tmp_path, AnyEmbedder())
    assert client.get(f"/api/agents/{a.id}").json()["index"]["cards"] == "missing"
    assert client.post(f"/api/agents/{a.id}/index-build").json() == {"ok": True}
    job = _wait_job(client, a.id)
    assert job["state"] == "ok", job["note"]
    assert "表卡" in job["note"] and "值索引" in job["note"]
    idx = client.get(f"/api/agents/{a.id}").json()["index"]
    assert idx["cards"] == "ok" and idx["values"] == "ok"
    assert (tmp_path / "idx").is_dir()  # 写的是注入的 tmp 目录，真实 data/indexes 零染指


# ── 在途 409（busy 语义沿 ask 在途锁先例）──────────────────────────

def test_busy_409_while_running(store, tmp_path, fixture_db, monkeypatch):
    a = _agent_with_db(store, fixture_db)
    started = threading.Event()
    release = threading.Event()

    def slow_build(db, emb, *, root=None):
        started.set()
        release.wait(timeout=5)
        return types.SimpleNamespace(tables=1, embedded=1, reused=0, dir=root)

    monkeypatch.setattr(cards, "build_index", slow_build)
    client = _client(store, tmp_path, AnyEmbedder())
    assert client.post(f"/api/agents/{a.id}/index-build").status_code == 200
    assert started.wait(timeout=5)
    res = client.post(f"/api/agents/{a.id}/index-build")
    assert res.status_code == 409 and "在途" in res.json()["detail"]
    release.set()
    assert _wait_job(client, a.id)["state"] == "ok"


# ── ADR-0005 零触扫描：问数路径永不建索引；建侧符号只住在管理门模块 ──

def test_query_path_never_builds():
    for mod in (graph_build, graph_nodes, web_sessions, eval_bird):
        src = inspect.getsource(mod)
        assert "build_index" not in src, mod.__name__
        assert "build_value_index" not in src, mod.__name__


def test_build_symbol_lives_in_mgmt_door_only():
    """建侧符号落位钉：web 运行面（app.py）零知情、管理门自有模块——
    M10 票 03 的 test_runtime_never_builds 零触钉靠此结构原样存活（feedback.py 先例）。"""
    from qadata.web import app as app_mod
    from qadata.web import index_build as door

    assert "build_value_index" in inspect.getsource(door)
    assert "build_value_index" not in inspect.getsource(app_mod)
