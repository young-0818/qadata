"""M7 票 05 web 侧专测：会话落盘（SessionStore）＋三层记忆组装＋API 会话面。

单元层钉存储纪律（懒建档、hex12 焊死穿越、坏文件如实报错不静默吞、删智能体连带
清会话）与 build_session_context 纯函数（M9 票 05 预算驱动窗口——K=5 降为默认
换算结果、failed 轮不给草稿、L1 只认最近一轮成功——fresh_topic 人肉闸已撤 owner
裁 2026-09-15 票 09，连续性模型隐式判）
＋result_head 摘要（全量行不进上下文在此源头钉）。
API 层钉：session_id 真值回显、轮次落盘两端点同源（_finish_ask 一个收口）、口径
优先级不随会话变（请求显式 > 智能体业务知识 > 空，叠加框已裁 owner 2026-09-15）、
在途锁升格 session_id 键（同会话拒、新会话放行）、回放＝问答本体＋trail 留痕
（M9 票 02 改判：精简入档补「回放控制台空白」老洞，旧档无尾键形状不动）、
会话 PATCH 端点已随票 09 撤除（405 钉）、旧档案残留 overlay/fresh_topic 键向后
兼容忽略。
图侧注入纪律在 tests/test_session_context.py，本文件不重复。
"""
import json
import threading
from pathlib import Path
from unittest.mock import ANY

import pytest
from fastapi.testclient import TestClient

from qadata.types import Answer, QueryResult
from qadata.web.agents import AgentNotFound, AgentStore
from qadata.web.app import create_app
from qadata.web.sessions import (
    Session,
    SessionNotFound,
    SessionStore,
    SessionStoreError,
    append_turn,
    build_session_context,
    new_session_id,
    result_head,
    trail_entry,
)
from tests.fakes import ScriptedLLM
from tests.test_web_api import (
    _CONTRACT_KEYS,
    _HAPPY_SCRIPT,
    _S,
    _S_CLAR,
    _agent_with_datasource,
    _client,
)
from tests.test_web_sse import _GatedLLM, _parse, _progress

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
    return AgentStore(tmp_path / "agents")


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
    sess = Session(id=_SID, turns=(_turn(_Q1), _turn("第二问", failed=True)))
    sessions.save(agent.id, sess)
    assert sessions.load(agent.id, _SID) == sess
    text = (_sessions_dir(store, agent.id) / f"{_SID}.yaml").read_text(encoding="utf-8")
    assert "第二问" in text and _Q1 in text  # 人可读可审（AgentStore 同款）
    assert "overlay" not in text  # 叠加框已裁（owner 裁 2026-09-15），不再写该键
    assert "fresh_topic" not in text  # 新话题闸已撤（票 09），新写档案不产该键
    assert "digest_" not in text  # M9 票 05：无摘要链＝尾键不写，文件形状与入档前逐字节一致


def test_legacy_revoked_keys_ignored_on_load(sessions, store, agent):
    """已撤机制的向后兼容：旧档案残留 overlay／fresh_topic 键——读取忽略、不炸不吞史。"""
    d = _sessions_dir(store, agent.id)
    d.mkdir(parents=True)
    (d / f"{_SID}.yaml").write_text(
        "id: aabbccddeeff\noverlay: 旧版口径叠加\nfresh_topic: true\nturns:\n"
        + "- question: 旧问\n  ts: '2026-09-14T10:00:00+08:00'\n  failed: false\n"
        "  row_count: 1\n  head: 标量值 7\n  answer: {sql: 'SELECT 7'}\n",
        encoding="utf-8")
    s = sessions.load(agent.id, _SID)
    assert not hasattr(s, "fresh_topic") and len(s.turns) == 1
    # 闸语义已死：残留 true 也不再清 L1（草稿照常装载）
    assert build_session_context(s)["draft"]["sql"] == "SELECT 7"


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


def _sess(*qs, failed_last=False):
    turns = []
    for i, q in enumerate(qs):
        if failed_last and i == len(qs) - 1:
            turns.append(_turn(q, failed=True))
        else:
            turns.append(_turn(q, ts=f"2026-09-14T10:00:{i:02d}+08:00"))
    return Session(id=_SID, turns=tuple(turns))


def test_budget_window_keeps_all_small_turns():
    """M9 票 05 换代：K=5 废除为规则——7 条小轮全在 token 预算窗内（滑出＝0＝
    懒补零触发），不再切窗丢史；出窗只由预算决定。"""
    ctx = build_session_context(_sess(*[f"问{i}" for i in range(7)]))
    assert [t["question"] for t in ctx["turns"]] == [f"问{i}" for i in range(7)]
    assert "digest_lines" not in ctx  # 无滑出＝无摘要，载荷形状与票 04 一致


def test_budget_window_five_fat_turns_default_k_five():
    """K=5 成默认换算结果的数值钉：票 04 现实最坏记忆行（实测 104 tok/行）×5 容得下、
    ×6 出（时间升序、最老先出窗）。"""
    fat = tuple({"question": f"20{20 + i} 年各月贷款违约户数是多少，按月份分组列出",
                 "ts": f"2026-09-14T10:00:{i:02d}+08:00", "failed": False, "row_count": 12,
                 "head": "头部 3 行：1998-01 | 3；1998-02 | 5；1998-03 | 2",
                 "answer": {"sql": "SELECT strftime('%Y-%m', loan.date) m, COUNT(*) FROM loan "
                                   "WHERE loan.status IN ('B','N') GROUP BY m ORDER BY m"}}
                for i in range(6))
    ctx = build_session_context(Session(id=_SID, turns=fat))
    assert len(ctx["turns"]) == 5  # 第 6 条肥行越预算出窗（滑出待懒补）
    assert ctx["turns"][0]["question"].startswith("2021")  # 最老先出、时间升序
    assert ctx["draft"]["sql"] == fat[-1]["answer"]["sql"]


def test_failed_last_turn_no_draft_but_line_kept():
    ctx = build_session_context(_sess("好轮", "坏轮", failed_last=True))
    assert ctx["draft"] is None
    assert ctx["turns"][-1] == {"question": "坏轮", "sql": None,
                                "row_count": None, "head": None, "failed": True}
    assert not ctx["turns"][0]["failed"]  # 好轮照常带 SQL


def test_append_turn_archives_everything():
    payload = dict.fromkeys(_CONTRACT_KEYS)
    sess = Session(id=_SID, turns=(_turn(_Q1),))
    out = append_turn(sess, "第二问", res=None, failed=True, payload=payload)
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
    assert set(body) == _CONTRACT_KEYS  # 14 字段形状不动（session_id 出真值）
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


def test_session_ask_free_of_evidence_kwarg(store, fixture_db, monkeypatch):
    """ADR-0008 会话面同款：带 session_id 的问，run_question 亦永不收 evidence kwarg
    （口径通道＝后端字典，会话只供记忆装载）。"""
    seen = []

    def fake_run(db_path, question, **kw):
        seen.append(kw)
        return Answer(conclusion="ok")

    monkeypatch.setattr("qadata.web.app.run_question", fake_run)
    a = _agent_with_datasource(store, fixture_db)
    client = _client(ScriptedLLM([]), store)
    client.post("/api/ask", json={"agent_id": a.id, "question": "题",
                                  "session_id": _SID})
    assert len(seen) == 1 and "evidence" not in seen[0]


def test_draft_always_fed_when_last_success_authority_in_prompt(store, fixture_db):
    """票 09 换防：人肉闸撤后草稿常给（上轮成功即喂），"用不用"的裁量在节头授权句
    ——连续性判定不再有机电状态，prompt 形状即完整契约。"""
    a = _agent_with_datasource(store, fixture_db)
    llm = ScriptedLLM(_HAPPY_SCRIPT * 2)
    client = _client(llm, store)
    client.post("/api/ask", json={"agent_id": a.id, "question": _Q1, "session_id": _SID})
    client.post("/api/ask", json={"agent_id": a.id, "question": "完全无关的新话题", "session_id": _SID})
    u2, g2 = llm.prompts[3], llm.prompts[4]
    assert "会话历史" in u2 and "与历史无关" in u2  # L2 在场＋授权句
    assert "上一轮 SQL" in g2 and "与上一问无关" in g2  # L1 照常喂＋授权句在场


def test_replay_shape_qa_only(store, fixture_db):
    a = _agent_with_datasource(store, fixture_db)
    client = _client(ScriptedLLM(_HAPPY_SCRIPT), store)
    ask = client.post("/api/ask",
                      json={"agent_id": a.id, "question": _Q1, "session_id": _SID}).json()
    r = client.get(f"/api/agents/{a.id}/sessions/{_SID}").json()
    # 回放形状：id/turns＋M8 票 03 改判的 pending（关态/无在途＝null，形状恒在）
    assert r["id"] == _SID and set(r) == {"id", "turns", "pending"} and r["pending"] is None
    assert len(r["turns"]) == 1
    t = r["turns"][0]
    # 回放＝问答本体＋trail 留痕（M9 票 02：旧「不入档」裁决就此翻转）
    assert set(t) == {"question", "failed", "ts", "answer", "trail"}
    assert t["question"] == _Q1 and t["answer"] == ask  # answer＝契约 payload 原样（含 chart）


def test_session_endpoint_errors(store, fixture_db):
    a = _agent_with_datasource(store, fixture_db)
    client = _client(None, store)
    assert client.get("/api/agents/deadbeef0000/sessions").status_code == 404
    assert client.get(f"/api/agents/{a.id}/sessions/00zz00000000").status_code == 404  # 非法 sid
    assert client.get(f"/api/agents/{a.id}/sessions/{_SID}").status_code == 404  # 无档不放
    assert client.get("/api/agents/deadbeef0000/sessions/" + _SID).status_code == 404
    # 会话 PATCH 端点整面撤除（票 09）——路由不存在的诚实形态
    assert client.patch(f"/api/agents/{a.id}/sessions/{_SID}",
                        json={"fresh_topic": True}).status_code == 405


def test_single_turn_ask_side_effect_free(store, fixture_db):
    a = _agent_with_datasource(store, fixture_db)
    client = _client(ScriptedLLM(_HAPPY_SCRIPT), store)
    body = client.post("/api/ask", json={"agent_id": a.id, "question": _Q1}).json()
    assert body["session_id"] is None  # 单轮关态照旧
    assert client.get(f"/api/agents/{a.id}/sessions").json() == {"sessions": []}


# ── M8 票 03 改判（经典 HITL）：澄清暂停→pending 恢复→服务端合成续答→不落盘照旧───


def _clar_blob(ask="「表现」指成绩还是违约率？"):
    return json.dumps({"question": "改写", "intent": {}, "clarification": ask},
                      ensure_ascii=False)


def test_hitl_clarification_round_trip(store, fixture_db):
    """暂停态住 checkpoint、指针住进程内：澄清轮照旧不落盘（懒建档连带＝连文件
    都不生）但回放经 pending 恢复；下一条消息原样发回＝服务端合成续答，归档题面
    ＝合成全句（题史自证从"前端气泡"迁到"会话档"，更诚实）。"""
    a = _agent_with_datasource(store, fixture_db)
    ask = "按入学年还是毕业年算？"
    llm = ScriptedLLM([_clar_blob(ask), _clar_blob(ask)] + _HAPPY_SCRIPT[1:])
    client = TestClient(create_app(llm=llm, settings=_S_CLAR, agents=store,
                                   static_dir="__no_such_dist_for_tests__"))
    body = client.post("/api/ask",
                       json={"agent_id": a.id, "question": _Q1, "session_id": _SID}).json()
    assert body["clarification"] == ask and body["failed"] is False
    assert llm.calls == 1  # 暂停＝1 次调用、零沙箱零账本（与直 END 形态同价）
    assert client.get(f"/api/agents/{a.id}/sessions").json() == {"sessions": []}
    assert not _sessions_dir(store, a.id).exists()  # 澄清轮无落盘无档
    # 懒建档无档＋有 pending＝空档放行（404 闸为恢复让路，无 pending 照旧不放）
    r = client.get(f"/api/agents/{a.id}/sessions/{_SID}").json()
    assert r["turns"] == [] and r["pending"] == {"question": _Q1, "clarification": ask}
    # 续答：消息原样发回，合成与重放在服务端（前端不再拼串）
    r2 = client.post("/api/ask", json={"agent_id": a.id, "question": "入学年",
                                       "session_id": _SID}).json()
    assert r2["clarification"] is None and r2["sql"] is not None
    assert llm.calls == 4  # 暂停 1＋续跑 3（understand 重放＝HITL 既定代价，如实入账）
    assert "补充说明：" in llm.prompts[2]  # generate 吃到的题面带标记（防循环闸同源）
    composed = f"{_Q1}补充说明：{ask} 入学年"
    turn = client.get(f"/api/agents/{a.id}/sessions/{_SID}").json()["turns"][0]
    assert turn["question"] == composed and turn["answer"]["sql"] == r2["sql"]
    assert client.get(f"/api/agents/{a.id}/sessions/{_SID}").json()["pending"] is None


def test_hitl_discard_pending_asks_fresh(store, fixture_db):
    """「新话题」通道：discard_pending 弃在途澄清、本条按新问走全新 thread——
    旧 pending 不会把新话题拼进旧题（盲拼病随无状态合成一并退役）。"""
    a = _agent_with_datasource(store, fixture_db)
    ask = "按入学年还是毕业年算？"
    llm = ScriptedLLM([_clar_blob(ask)] + _HAPPY_SCRIPT)
    client = TestClient(create_app(llm=llm, settings=_S_CLAR, agents=store,
                                   static_dir="__no_such_dist_for_tests__"))
    b1 = client.post("/api/ask",
                     json={"agent_id": a.id, "question": _Q1, "session_id": _SID}).json()
    assert b1["clarification"] == ask
    b2 = client.post("/api/ask", json={"agent_id": a.id, "question": "完全换个题",
                                       "session_id": _SID, "discard_pending": True}).json()
    assert b2["clarification"] is None and b2["sql"] is not None
    turn = client.get(f"/api/agents/{a.id}/sessions/{_SID}").json()["turns"][0]
    assert turn["question"] == "完全换个题"  # 归档是用户真话，不是合成串
    assert llm.calls == 4  # 暂停 1＋新问全链 3（不是续答的 3——thread 全新）


def test_hitl_pending_survives_on_stream_endpoint(store, fixture_db):
    """SSE 端点同经 _route_clarification（唯一分流闸口）：暂停→指针→续答两端点同形。"""
    a = _agent_with_datasource(store, fixture_db)
    ask = "按入学年还是毕业年算？"
    llm = ScriptedLLM([_clar_blob(ask), _clar_blob(ask)] + _HAPPY_SCRIPT[1:])
    client = TestClient(create_app(llm=llm, settings=_S_CLAR, agents=store,
                                   static_dir="__no_such_dist_for_tests__"))
    _, data = _parse(client.post("/api/ask/stream", json={"agent_id": a.id,
                                                          "question": _Q1, "session_id": _SID}).text)[-1]
    assert data["clarification"] == ask and set(data) == _CONTRACT_KEYS
    _, data2 = _parse(client.post("/api/ask/stream", json={"agent_id": a.id,
                                                           "question": "毕业年", "session_id": _SID}).text)[-1]
    assert data2["clarification"] is None and data2["sql"] is not None
    assert client.get(f"/api/agents/{a.id}/sessions/{_SID}").json()["turns"][0]["question"] \
        == f"{_Q1}补充说明：{ask} 毕业年"


def test_clarification_off_state_replay_pending_null(store, fixture_db):
    """关态逐行为一致的形状钉：回放响应带 pending 字段但恒 null；无档无 pending 照旧 404。"""
    a = _agent_with_datasource(store, fixture_db)
    client = _client(ScriptedLLM(_HAPPY_SCRIPT), store)
    client.post("/api/ask", json={"agent_id": a.id, "question": _Q1, "session_id": _SID})
    assert client.get(f"/api/agents/{a.id}/sessions/{_SID}").json()["pending"] is None
    assert client.get(f"/api/agents/{a.id}/sessions/000000000000").status_code == 404


def test_stream_session_persists_and_echoes(store, fixture_db):
    a = _agent_with_datasource(store, fixture_db)
    llm = ScriptedLLM(_HAPPY_SCRIPT)
    res = _client(llm, store).post("/api/ask/stream",
                                   json={"agent_id": a.id, "question": _Q1, "session_id": _SID})
    ev, data = _parse(res.text)[-1]
    assert ev == "answer" and data["session_id"] == _SID and set(data) == _CONTRACT_KEYS
    out = _client(None, store).get(f"/api/agents/{a.id}/sessions").json()["sessions"]
    assert out[0]["id"] == _SID and out[0]["turn_count"] == 1  # 落盘与阻塞端点同源


# ── M9 票 02：trail 精简入档＋回放留痕透传 ─────────────────────────────


def test_trail_entry_frames_pass_through_thinking_rejected():
    """thinking 不入档专测＋形状单源：步骤帧/tool 帧**原样**入档（trail 条目即帧
    本身——不抄键即无第二处字面可漂，前端回放按直播同一联合类型直读），thinking 拒收。"""
    start = {"node": "understand", "attempt": 0, "status": "start"}
    result = {"node": "execute", "attempt": 1, "status": "执行成功：3 行",
              "ok": True, "duration_ms": 42, "tokens_in": 0, "tokens_out": 0}
    tool = {"node": "explore", "kind": "tool", "tool": "list_tables", "ok": True,
            "duration_ms": 2}
    assert all(trail_entry(f) is f for f in (start, result, tool))
    assert trail_entry({"node": "generate", "kind": "thinking", "text": "唔"}) is None


def test_append_turn_without_trail_byte_identical(monkeypatch):
    """缺省/空 trail＝轮条目与入档前逐字节一致——eval/CLI 根本不经 append_turn、
    无会话 web 请求传 None，落盘面「不写」在此钉死。"""
    monkeypatch.setattr("qadata.web.sessions.turn_ts",
                        lambda: "2026-09-18T00:00:00+08:00")
    payload = dict.fromkeys(_CONTRACT_KEYS)
    a = append_turn(Session(id=_SID), _Q1, res=None, failed=False, payload=payload)
    b = append_turn(Session(id=_SID), _Q1, res=None, failed=False, payload=payload,
                    trail=[])
    assert a.turns == b.turns and "trail" not in a.turns[0]


def test_stream_trail_archived_equals_live_frames(store, fixture_db):
    """帧形单源端到端钉：SSE 直播帧流的非 thinking 帧 ＝＝ 档案轮 trail（同帧同形，
    回放 Console 原样消费）；thinking 在场于直播、缺席于档案。"""
    a = _agent_with_datasource(store, fixture_db)
    llm = ScriptedLLM(_HAPPY_SCRIPT,
                      reasonings=["先把问题改写成可查的形式", "核对表列", None])
    res = _client(llm, store).post(
        "/api/ask/stream",
        json={"agent_id": a.id, "question": _Q1, "session_id": _SID})
    frames = _progress(_parse(res.text))
    assert any(f.get("kind") == "thinking" for f in frames)  # thinking 确在直播面
    turn = SessionStore(store).load(a.id, _SID).turns[0]
    assert turn["trail"] == [f for f in frames if f.get("kind") != "thinking"]
    assert list(turn)[-1] == "trail"  # 可选尾键（chart/feedback 先例位）


def test_blocking_ask_archives_trail_without_streaming(store, fixture_db):
    """阻塞端点无直播消费者：trail 照样入档（会话档管「用户当时看见什么」一视同仁），
    但入档观测不开流式旁路——timed_invoke 形态姊妹钉 stream_used==0（M8 票 08 纪律）。"""
    a = _agent_with_datasource(store, fixture_db)
    llm = ScriptedLLM(_HAPPY_SCRIPT)
    _client(llm, store).post("/api/ask",
                             json={"agent_id": a.id, "question": _Q1, "session_id": _SID})
    assert llm.stream_used == 0
    turn = SessionStore(store).load(a.id, _SID).turns[0]
    assert [f["node"] for f in turn["trail"]][:2] == ["understand", "understand"]
    assert all(f.get("kind") != "thinking" for f in turn["trail"])


def test_replay_entry_trail_passthrough_and_legacy_files(store, fixture_db):
    """回放接口 payload 兼容：新档 trail 透传；旧档（票 02 前、无尾键）形状不动。"""
    a = _agent_with_datasource(store, fixture_db)
    client = _client(ScriptedLLM(_HAPPY_SCRIPT), store)
    client.post("/api/ask", json={"agent_id": a.id, "question": _Q1, "session_id": _SID})
    t = client.get(f"/api/agents/{a.id}/sessions/{_SID}").json()["turns"][0]
    assert set(t) == {"question", "failed", "ts", "answer", "trail"} and t["trail"]
    SessionStore(store).save(a.id, Session(id="0123456789ab", turns=(_turn("旧问"),)))
    tl = client.get(f"/api/agents/{a.id}/sessions/0123456789ab").json()["turns"][0]
    assert set(tl) == {"question", "failed", "ts", "answer"}


def test_hitl_resume_archives_trail_without_streaming(store, fixture_db):
    """续答轮（暂停 1＋续跑 3＝合成归档一轮）也入 trail；且全程 timed_invoke——
    trail_sink 的 qadata_no_stream 标经 mirror 双包照传（obs 开态链路有
    test_mirror_propagates_no_stream_marker 单钉，此处端到端 stream_used==0）。"""
    a = _agent_with_datasource(store, fixture_db)
    ask = "按入学年还是毕业年算？"
    llm = ScriptedLLM([_clar_blob(ask), _clar_blob(ask)] + _HAPPY_SCRIPT[1:])
    client = TestClient(create_app(llm=llm, settings=_S_CLAR, agents=store,
                                   static_dir="__no_such_dist_for_tests__"))
    client.post("/api/ask", json={"agent_id": a.id, "question": _Q1, "session_id": _SID})
    client.post("/api/ask", json={"agent_id": a.id, "question": "入学年",
                                  "session_id": _SID})
    assert llm.calls == 4 and llm.stream_used == 0
    turn = SessionStore(store).load(a.id, _SID).turns[0]
    assert turn["trail"] and all(f.get("kind") != "thinking" for f in turn["trail"])


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
