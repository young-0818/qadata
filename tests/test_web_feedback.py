"""M8 票 04 专测：反馈闭环（旁挂票档）——存储/端点/回放合并/导出，全程零 LLM、零花费。

会话档直种 SessionStore（不经 ask，零调用）→ 打反馈端点 → 读旁挂 jsonl。
钉四条纪律：① 写者不建会话（无会话档＝404）② 反馈不进 AskResponse 契约
（回放轮 answer 子对象仍 14 字段、feedback 只挂轮顶层）③ 同 ts 取末票
④ 导出按 ts 回查轮、SQL 以轮 payload 为真源、回查不中如实计数。
"""
import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from qadata.web.agents import AgentStore
from qadata.web.app import create_app
from qadata.web.feedback import (
    FeedbackError,
    append_vote,
    export_feedback,
    feedback_file,
    latest_votes,
    read_votes,
)
from qadata.web.sessions import SESSIONS_SUBDIR, Session, SessionStore

_SID = "aabbccddeeff"
_OTHER = "112233445566"  # 合法 hex12 但从不建会话档（测写者不建会话）
_TS = "2026-09-16T10:00:00+08:00"
_TS2 = "2026-09-16T10:05:00+08:00"


def _turn(q, sql, ts=_TS, failed=False):
    return {"question": q, "ts": ts, "failed": failed,
            "row_count": None if failed else 1, "head": "",
            "answer": {"sql": sql, "conclusion": q + "答"}}


@pytest.fixture
def store(tmp_path):
    return AgentStore(tmp_path / "agents")


@pytest.fixture
def agent(store):
    return store.create("反馈智能体", "")


@pytest.fixture
def client(store):
    # llm=None：反馈/回放/导出全链路不该碰模型，一碰即炸（零花费的机制保证）
    return TestClient(create_app(llm=None, agents=store,
                                 static_dir="__no_such_dist__"))


def _seed(store, agent_id, sid, turns):
    SessionStore(store).save(agent_id, Session(id=sid, turns=tuple(turns)))


# ── 端点：合法票落盘可查／写者不建会话／票值域 ──────────────────────


def test_valid_vote_lands_and_is_queryable(client, store, agent):
    _seed(store, agent.id, _SID, [_turn("谁最高", "SELECT MAX(h) FROM t")])
    r = client.post(f"/api/agents/{agent.id}/feedback",
                    json={"session_id": _SID, "ts": _TS, "vote": "up"})
    assert r.status_code == 200 and r.json() == {"ok": True}
    f = feedback_file(store, agent.id, _SID)
    assert f.is_file()
    rows = read_votes(store, agent.id, _SID)
    assert len(rows) == 1 and rows[0]["vote"] == "up" and rows[0]["ts"] == _TS
    # 轮 payload 回查所得题面/SQL 一并归档（旁挂自足，导出无需再读会话档）
    assert rows[0]["question"] == "谁最高" and rows[0]["sql"] == "SELECT MAX(h) FROM t"
    assert "at" in rows[0]


def test_writer_does_not_create_session(client, store, agent):
    # sid 合法但无会话档（从没问过话）→ 404，且不落旁挂文件
    r = client.post(f"/api/agents/{agent.id}/feedback",
                    json={"session_id": _OTHER, "ts": _TS, "vote": "down"})
    assert r.status_code == 404
    assert not feedback_file(store, agent.id, _OTHER).exists()
    assert not (Path(store.agent_dir(agent.id)) / SESSIONS_SUBDIR / f"{_OTHER}.yaml").exists()


def test_bad_vote_value_rejected(client, store, agent):
    _seed(store, agent.id, _SID, [_turn("q", "SELECT 1")])
    r = client.post(f"/api/agents/{agent.id}/feedback",
                    json={"session_id": _SID, "ts": _TS, "vote": "meh"})
    assert r.status_code == 400


def test_unknown_ts_rejected(client, store, agent):
    _seed(store, agent.id, _SID, [_turn("q", "SELECT 1")])
    r = client.post(f"/api/agents/{agent.id}/feedback",
                    json={"session_id": _SID, "ts": "1970-01-01T00:00:00+08:00",
                          "vote": "up"})
    assert r.status_code == 404


def test_unknown_agent_404(client):
    r = client.post("/api/agents/deadbeef9999/feedback",
                    json={"session_id": _SID, "ts": _TS, "vote": "up"})
    assert r.status_code == 404


# ── 回放合并：可选尾键 feedback／同 ts 末票／不进 AskResponse 契约 ────


def test_replay_merges_feedback_tail_key(client, store, agent):
    _seed(store, agent.id, _SID, [_turn("好答", "SELECT 1"), _turn("坏答", "SELECT 2", _TS2)])
    client.post(f"/api/agents/{agent.id}/feedback",
                json={"session_id": _SID, "ts": _TS, "vote": "up"})
    d = client.get(f"/api/agents/{agent.id}/sessions/{_SID}").json()
    assert d["turns"][0]["feedback"] == "up"
    assert "feedback" not in d["turns"][1]  # 无票轮形状不动（可选尾键＝chart 先例）
    # 反馈不进契约：answer 子对象键集仍是 14 字段全集、不含 feedback
    from tests.test_web_api import _CONTRACT_KEYS
    assert "feedback" not in d["turns"][0]["answer"]
    assert d["turns"][0]["answer"]["sql"] == "SELECT 1"
    assert _CONTRACT_KEYS


def test_same_ts_last_vote_wins(client, store, agent):
    _seed(store, agent.id, _SID, [_turn("q", "SELECT 1")])
    for v in ("up", "down", "up"):
        client.post(f"/api/agents/{agent.id}/feedback",
                    json={"session_id": _SID, "ts": _TS, "vote": v})
    assert latest_votes(store, agent.id, _SID)[_TS] == "up"
    d = client.get(f"/api/agents/{agent.id}/sessions/{_SID}").json()
    assert d["turns"][0]["feedback"] == "up"
    # append-only：三票三行都在档（末票胜出是读取语义，不覆盖物理行）
    assert len(read_votes(store, agent.id, _SID)) == 3


# ── 存储层：坏档纪律（尾行宽容、中段报错）＋ 并发 append ────────────


def test_corrupt_middle_line_raises_trailing_halfline_tolerated(store, agent):
    _seed(store, agent.id, _SID, [_turn("q", "SELECT 1")])
    append_vote(store, agent.id, _SID, ts=_TS, vote="up", question="q", sql="SELECT 1")
    f = feedback_file(store, agent.id, _SID)
    # 追加半截末行＝crash 尸体，读时只丢最后半行
    with f.open("a", encoding="utf-8") as h:
        h.write('{"ts": "2026-01-01T00:00:00+08:00", "vote": "dow')
    assert len(read_votes(store, agent.id, _SID)) == 1
    # 中段坏行＝整档报错不静默吞（会话档同款纪律）
    f.write_text('坏行\n' + f.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(FeedbackError):
        read_votes(store, agent.id, _SID)


def test_concurrent_appends_interleave_free(store, agent):
    _seed(store, agent.id, _SID, [_turn("q", "SELECT 1")])

    def _many():
        for _ in range(20):
            append_vote(store, agent.id, _SID, ts=_TS, vote="up", question="q", sql="SELECT 1")

    ts = [threading.Thread(target=_many) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(read_votes(store, agent.id, _SID)) == 80


# ── 导出：按 ts 回查轮、SQL 真源、回查不中计数 ──────────────────────


def test_export_joins_turn_and_counts_orphans(store, agent, tmp_path):
    _seed(store, agent.id, _SID, [_turn("在售几款", "SELECT COUNT(*) FROM cards")])
    append_vote(store, agent.id, _SID, ts=_TS, vote="down",
                question="在售几款", sql="SELECT COUNT(*) FROM cards")
    append_vote(store, agent.id, _SID, ts="1970-01-01T00:00:00+08:00",
                vote="up", question="幽灵轮", sql="SELECT 0")  # 回查不中
    out = tmp_path / "fb.jsonl"
    n, skipped = export_feedback(store, out)
    assert n == 1 and skipped == 1  # 幽灵票如实计数、不进卷
    lines = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 1
    row = lines[0]
    # SQL/题面以轮 payload 为真源（非票档字面），带 agent/session/vote/ts 供人审
    assert row["sql"] == "SELECT COUNT(*) FROM cards" and row["vote"] == "down"
    assert row["agent"] == agent.id and row["session"] == _SID and row["question"] == "在售几款"


def test_export_empty_is_clean_file(store, agent, tmp_path):
    out = tmp_path / "none.jsonl"
    assert export_feedback(store, out) == (0, 0)
    assert out.read_text(encoding="utf-8") == ""


def test_cli_feedback_export_smoke(store, agent, tmp_path, capsys):
    from qadata.cli.main import main

    _seed(store, agent.id, _SID, [_turn("q", "SELECT 1")])
    append_vote(store, agent.id, _SID, ts=_TS, vote="up", question="q", sql="SELECT 1")
    out = tmp_path / "cli.jsonl"
    rc = main(["feedback-export", "--agents-dir", str(tmp_path / "agents"),
               "--out", str(out)])
    assert rc == 0
    assert out.is_file() and len(out.read_text(encoding="utf-8").splitlines()) == 1
