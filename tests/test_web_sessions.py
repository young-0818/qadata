"""M7 票 05 web 侧专测：会话落盘（SessionStore）＋三层记忆组装＋API 会话面。

单元层钉存储纪律（懒建档、hex12 焊死穿越、坏文件如实报错不静默吞、删智能体连带
清会话）与 build_session_context 纯函数（K=5 滑窗、failed 轮不给草稿、fresh_topic
清 L1 保 L2 且只挡一次）＋result_head 摘要（全量行不进上下文在此源头钉）。
API 层钉：session_id 真值回显、轮次落盘两端点同源（_finish_ask 一个收口）、口径
优先级＝请求显式 > 会话叠加＋业务知识拼接 > 空（确定性合并零 LLM）、在途锁升格
session_id 键（同会话拒、新会话放行）、回放＝问答本体无 trail（owner 裁决）。
图侧注入纪律在 tests/test_session_context.py，本文件不重复。
"""
import threading
from pathlib import Path
from unittest.mock import ANY

import pytest
from fastapi.testclient import TestClient

from qadata.types import Answer, QueryResult
from qadata.web.agents import AgentNotFound, AgentStore
from qadata.web.app import create_app
from qadata.web.sessions import (
    SESSION_MEMORY_K,
    Session,
    SessionNotFound,
    SessionStore,
    SessionStoreError,
    append_turn,
    build_session_context,
    new_session_id,
    result_head,
)
from tests.fakes import ScriptedLLM
from tests.test_web_api import (
    _CONTRACT_KEYS,
    _HAPPY_SCRIPT,
    _S,
    _agent_with_datasource,
    _client,
)
from tests.test_web_sse import _GatedLLM, _parse

_SID = "aabbccddeeff"
_Q1 = "2026 年有多少新生"
_SQL1 = "SELECT COUNT(*) FROM student WHERE grad=2026"


def _turn(q, sql=_SQL1, failed=False, row_count=1, head="标量值 120", ts="2026-09-14T10:00:00+08:00"):
    return {"question": q, "ts": ts, "failed": failed,
            "row_count": None if failed else row_count,
            "head": "" if failed else head, "answer": {"sql": None if failed else sql}}


# ── SessionStore：懒建档／roundtrip／诚实报错 ───────────────────────


@pytest.fixture
def store(tmp_path):
    return AgentStore(tmp_path / "agents", metrics_dir=tmp_path / "metrics")


@pytest.fixture
def sessions(store):
    return SessionStore(store)


@pytest.fixture
def agent(store):
    return store.create("会话智能体", "")


def _sessions_dir(store, agent_id):
    return Path(store.agent_dir(agent_id)) / "sessions"


def test_lazy_load_creates_nothing(sessions, store, agent):
    s = sessions.load(agent.id, _SID)
    assert s == Session(id=_SID)
    assert not _sessions_dir(store, agent.id).exists()  # 首问落盘才建文件


def test_save_load_roundtrip(sessions, store, agent):
    sess = Session(id=_SID, overlay="口径 X", fresh_topic=True,
                   turns=(_turn(_Q1), _turn("第二问", failed=True)))
    sessions.save(agent.id, sess)
    assert sessions.load(agent.id, _SID) == sess
    text = (_sessions_dir(store, agent.id) / f"{_SID}.yaml").read_text(encoding="utf-8")
    assert "口径 X" in text and _Q1 in text  # 人可读可审（AgentStore 同款）


def test_id_and_agent_guards(sessions, store, agent):
    with pytest.raises(SessionNotFound):
        sessions.load(agent.id, "../etc/passwd")
    with pytest.raises(SessionNotFound):
        sessions.load(agent.id, "ZZZZ12345678")  # 大写非 hex 也拒
    with pytest.raises(AgentNotFound):
        sessions.load("deadbeef9999", _SID)  # 智能体不存在
    with pytest.raises(AgentNotFound):
        sessions.save("deadbeef9999", Session(id=_SID))  # 写侧同闸（防目录僵尸）


def test_broken_files_raise_not_swallow(sessions, store, agent):
    d = _sessions_dir(store, agent.id)
    d.mkdir(parents=True)
    (d / f"{_SID}.yaml").write_text("id: aabbccddeeff\nturns: 不是列表\n", encoding="utf-8")
    with pytest.raises(SessionStoreError):
        sessions.load(agent.id, _SID)
    with pytest.raises(SessionStoreError):
        sessions.list(agent.id)  # 整列报错——坏会话不装没看见
    (d / "000000000000.yaml").write_text("id: 000000000000\noverlay: ''\nturns:\n  - question: 缺形状\n",
                                         encoding="utf-8")
    with pytest.raises(SessionStoreError):
        sessions.load(agent.id, "000000000000")  # 轮次形状不正
    with pytest.raises(SessionStoreError):
        sessions.load(agent.id, _SID)  # 换坏文件仍炸（不被前一个掩盖）


def test_delete_agent_wipes_sessions(store, agent):
    sessions = SessionStore(store)
    sessions.save(agent.id, Session(id=_SID, turns=(_turn(_Q1),)))
    assert _sessions_dir(store, agent.id).is_dir()
    store.delete(agent.id)
    with pytest.raises(AgentNotFound):
        sessions.list(agent.id)  # 连带清会话＝目录随智能体入土


def test_list_skips_empty_and_sorts(sessions, store, agent):
    sessions.save(agent.id, Session(id="111111111111"))  # 无轮＝不占侧栏
    t = _turn(_Q1)
    sessions.save(agent.id, Session(id="222222222222", turns=(t,), ))
    old = dict(t, ts="2026-01-01T00:00:00+08:00")
    sessions.save(agent.id, Session(id="333333333333", turns=(old,)))
    out = sessions.list(agent.id)
    assert [d["id"] for d in out] == ["222222222222", "333333333333"]  # updated 新→旧
    assert out[0]["title"] == _Q1 and out[0]["turn_count"] == 1


# ── 三层记忆组装（纯函数）──────────────────────────────────────────


def test_context_empty_is_none():
    assert build_session_context(Session(id=_SID)) is None


def _sess(*qs, failed_last=False, fresh=False):
    turns = []
    for i, q in enumerate(qs):
        if failed_last and i == len(qs) - 1:
            turns.append(_turn(q, failed=True))
        else:
            turns.append(_turn(q, ts=f"2026-09-14T10:00:{i:02d}+08:00"))
    return Session(id=_SID, fresh_topic=fresh, turns=tuple(turns))


def test_l2_window_is_last_five_chronological():
    ctx = build_session_context(_sess(*[f"问{i}" for i in range(7)]))
    assert len(ctx["turns"]) == SESSION_MEMORY_K
    assert [t["question"] for t in ctx["turns"]] == ["问2", "问3", "问4", "问5", "问6"]  # 时间升序，q0/q1 出窗
    assert ctx["draft"]["sql"] == _SQL1  # L3 全史在案，出窗≠丢失（归档归文件）


def test_failed_last_turn_no_draft_but_line_kept():
    ctx = build_session_context(_sess("好轮", "坏轮", failed_last=True))
    assert ctx["draft"] is None
    assert ctx["turns"][-1] == {"question": "坏轮", "sql": None,
                                "row_count": None, "head": None, "failed": True}
    assert not ctx["turns"][0]["failed"]  # 好轮照常带 SQL


def test_fresh_topic_clears_l1_keeps_l2_once():
    ctx = build_session_context(_sess(_Q1, fresh=True))
    assert ctx["draft"] is None  # L1 清
    assert len(ctx["turns"]) == 1 and ctx["turns"][0]["question"] == _Q1  # L2 保


def test_append_turn_resets_fresh_and_archives_everything():
    payload = dict.fromkeys(_CONTRACT_KEYS)
    sess = Session(id=_SID, fresh_topic=True, turns=(_turn(_Q1),))
    out = append_turn(sess, "第二问", res=None, failed=True, payload=payload)
    assert out.fresh_topic is False  # 新话题只挡下一问一次
    assert len(out.turns) == 2  # L3 全史累积（失败轮也入账）
    assert out.turns[1]["failed"] is True and out.turns[1]["head"] == ""


def test_result_head_forms():
    assert result_head(None) == "0 行（未查询到数据）"
    empty = QueryResult(columns=["a"], rows=[], row_count=0, truncated=False, elapsed_ms=1)
    assert result_head(empty) == "0 行（未查询到数据）"
    scalar = QueryResult(columns=["n"], rows=[(120,)], row_count=1, truncated=False, elapsed_ms=1)
    assert result_head(scalar) == "标量值 120"
    rows = QueryResult(columns=["s", "v"], rows=[(str(i), i) for i in range(9)],
                       row_count=42, truncated=True, elapsed_ms=1)
    h = result_head(rows)
    assert h.startswith("头部 3 行：0 | 0；1 | 1；2 | 2") and h.endswith("…")
    assert "8 | 8" not in h  # 全量行不进摘要（不进上下文）


# ── API：会话面 ─────────────────────────────────────────────────────


def test_ask_echoes_session_and_persists_turn(store, fixture_db):
    a = _agent_with_datasource(store, fixture_db)
    client = _client(ScriptedLLM(_HAPPY_SCRIPT), store)
    body = client.post("/api/ask",
                       json={"agent_id": a.id, "question": _Q1, "session_id": _SID}).json()
    assert set(body) == _CONTRACT_KEYS  # 13 字段形状不动（session_id 出真值）
    assert body["session_id"] == _SID
    out = client.get(f"/api/agents/{a.id}/sessions").json()["sessions"]
    assert out == [{"id": _SID, "title": _Q1, "updated": ANY, "turn_count": 1}]


def test_second_ask_carries_l2_and_l1(store, fixture_db):
    a = _agent_with_datasource(store, fixture_db)
    llm = ScriptedLLM(_HAPPY_SCRIPT + ["改写", "SELECT grade FROM students LIMIT 1", "二年级"])
    client = _client(llm, store)
    client.post("/api/ask", json={"agent_id": a.id, "question": _Q1, "session_id": _SID})
    client.post("/api/ask", json={"agent_id": a.id, "question": "这些新生年级如何", "session_id": _SID})
    prev_sql = _HAPPY_SCRIPT[1]  # "SELECT name FROM students WHERE id = 2"
    u2, g2 = llm.prompts[3], llm.prompts[4]
    assert "会话历史" in u2 and _Q1 in u2  # L2：问题进消解
    assert prev_sql in u2 and "标量值 Bob" in u2  # L2：上轮 SQL＋标量头部
    assert "上一轮 SQL" in g2 and prev_sql in g2 and "标量值 Bob" in g2  # L1 草稿进 generate 尾部
    assert llm.calls == 6  # 成本条款：多轮与单轮同调用数（3＋3）


def test_overlay_merge_priority(store, fixture_db, monkeypatch):
    """口径优先级＝请求显式 > 会话叠加＋智能体业务知识拼接 > 空（拼接非覆盖）。"""
    seen = []

    def fake_run(db_path, question, evidence="", **kw):
        seen.append(evidence)
        return Answer(conclusion="ok")

    monkeypatch.setattr("qadata.web.app.run_question", fake_run)
    a = _agent_with_datasource(store, fixture_db, evidence="库口径")
    client = _client(ScriptedLLM([]), store)
    base = {"agent_id": a.id, "question": "题", "session_id": _SID}
    client.post("/api/ask", json=base)
    client.patch(f"/api/agents/{a.id}/sessions/{_SID}", json={"overlay": "会话口径"})
    client.post("/api/ask", json=base)
    client.post("/api/ask", json={**base, "evidence": "显式口径"})
    b = store.create("空口径", "")
    store.store_datasource(b.id, b"x", "s.sqlite")
    client.post("/api/ask", json={"agent_id": b.id, "question": "题"})
    assert seen == ["库口径", "会话口径\n库口径", "显式口径", ""]
    replay = client.get(f"/api/agents/{a.id}/sessions/{_SID}").json()
    assert replay["overlay"] == "会话口径"  # 叠加随会话落盘


def test_new_topic_endpoint_flow_and_reset(store, fixture_db):
    a = _agent_with_datasource(store, fixture_db)
    llm = ScriptedLLM(_HAPPY_SCRIPT * 3)
    client = _client(llm, store)
    client.post("/api/ask", json={"agent_id": a.id, "question": _Q1, "session_id": _SID})
    assert client.patch(f"/api/agents/{a.id}/sessions/{_SID}",
                        json={"fresh_topic": True}).json()["fresh_topic"] is True
    client.post("/api/ask", json={"agent_id": a.id, "question": "换个话题", "session_id": _SID})
    assert "上一轮 SQL" not in llm.prompts[4]  # L1 清
    assert "会话历史" in llm.prompts[3]  # L2 保
    assert client.get(f"/api/agents/{a.id}/sessions/{_SID}").json()["fresh_topic"] is False  # 复位入账
    client.post("/api/ask", json={"agent_id": a.id, "question": "接着问", "session_id": _SID})
    assert "上一轮 SQL" in llm.prompts[7]  # 新话题只挡一次，第三问草稿照常（草稿=第二问）


def test_replay_shape_qa_only(store, fixture_db):
    a = _agent_with_datasource(store, fixture_db)
    client = _client(ScriptedLLM(_HAPPY_SCRIPT), store)
    ask = client.post("/api/ask",
                      json={"agent_id": a.id, "question": _Q1, "session_id": _SID}).json()
    r = client.get(f"/api/agents/{a.id}/sessions/{_SID}").json()
    assert r["id"] == _SID and r["overlay"] == ""
    assert len(r["turns"]) == 1
    t = r["turns"][0]
    assert set(t) == {"question", "failed", "ts", "answer"}  # 回放＝问答本体（trail 不入档）
    assert t["question"] == _Q1 and t["answer"] == ask  # answer＝契约 payload 原样（含 chart）


def test_session_endpoint_errors(store, fixture_db):
    a = _agent_with_datasource(store, fixture_db)
    client = _client(None, store)
    assert client.get("/api/agents/deadbeef0000/sessions").status_code == 404
    assert client.get(f"/api/agents/{a.id}/sessions/00zz00000000").status_code == 404  # 非法 sid
    assert client.get(f"/api/agents/{a.id}/sessions/{_SID}").status_code == 404  # 无档不放
    long = "超" * 2001
    assert client.patch(f"/api/agents/{a.id}/sessions/{_SID}",
                        json={"overlay": long}).status_code == 400
    assert client.get("/api/agents/deadbeef0000/sessions/" + _SID).status_code == 404


def test_overlay_patch_during_inflight_ask_not_lost(store, fixture_db, monkeypatch):
    """双轴评审收紧（读改写竞态）：在途问答期间 PATCH 改叠加/新话题，落盘走
    写前重载合并——PATCH 不丢；在途新设的新话题本问未消费，原样留给下一问。"""
    a = _agent_with_datasource(store, fixture_db)
    client = _client(ScriptedLLM([]), store)  # llm 不被触达（run 被 mock）

    def fake_run(db_path, question, evidence="", **kw):
        client.patch(f"/api/agents/{a.id}/sessions/{_SID}",
                     json={"overlay": "中途叠加", "fresh_topic": True})
        return Answer(conclusion="ok")

    monkeypatch.setattr("qadata.web.app.run_question", fake_run)
    client.post("/api/ask", json={"agent_id": a.id, "question": "题", "session_id": _SID})
    sess = client.get(f"/api/agents/{a.id}/sessions/{_SID}").json()
    assert sess["overlay"] == "中途叠加"
    assert sess["fresh_topic"] is True
    assert len(sess["turns"]) == 1


def test_single_turn_ask_side_effect_free(store, fixture_db):
    a = _agent_with_datasource(store, fixture_db)
    client = _client(ScriptedLLM(_HAPPY_SCRIPT), store)
    body = client.post("/api/ask", json={"agent_id": a.id, "question": _Q1}).json()
    assert body["session_id"] is None  # 单轮关态照旧
    assert client.get(f"/api/agents/{a.id}/sessions").json() == {"sessions": []}


def test_stream_session_persists_and_echoes(store, fixture_db):
    a = _agent_with_datasource(store, fixture_db)
    llm = ScriptedLLM(_HAPPY_SCRIPT)
    res = _client(llm, store).post("/api/ask/stream",
                                   json={"agent_id": a.id, "question": _Q1, "session_id": _SID})
    ev, data = _parse(res.text)[-1]
    assert ev == "answer" and data["session_id"] == _SID and set(data) == _CONTRACT_KEYS
    out = _client(None, store).get(f"/api/agents/{a.id}/sessions").json()["sessions"]
    assert out[0]["id"] == _SID and out[0]["turn_count"] == 1  # 落盘与阻塞端点同源


def test_inflight_lock_key_upgraded_to_session(store, fixture_db):
    """同会话并发拒（两端点一把锁）；换会话号即放行（单用户同智能体多会话合法并存）。"""
    a = _agent_with_datasource(store, fixture_db)
    gated = _GatedLLM(_HAPPY_SCRIPT * 3)
    app = create_app(llm=gated, settings=_S, agents=store,
                     static_dir="__no_such_dist_for_tests__")
    body = {"agent_id": a.id, "question": _Q1, "session_id": _SID}
    held: list = []
    holder = threading.Thread(target=lambda: held.append(
        TestClient(app).post("/api/ask/stream", json=body)))
    holder.start()
    try:
        assert gated.entered.wait(5)  # 流式题在途（会话锁持有中）
        res = TestClient(app).post("/api/ask", json=body)
        assert res.status_code == 409 and "正在回答上一个问题" in res.json()["detail"]
        res = TestClient(app).post("/api/ask/stream", json=body)
        assert res.status_code == 409
        assert gated.calls == 0
    finally:
        gated.gate.set()
    holder.join(20)
    assert not holder.is_alive() and held[0].status_code == 200
    # 另一会话号：锁键不同即放行（calls 3→6）
    res = TestClient(app).post("/api/ask", json={**body, "session_id": new_session_id()})
    assert res.status_code == 200 and res.json()["failed"] is False
