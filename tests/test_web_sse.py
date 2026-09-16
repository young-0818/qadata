"""M7 票 03 契约测试：`POST /api/ask/stream`（SSE 进度帧）＋在途锁。

帧三型（M8 票 06 喂厚后）：start＝node/attempt/status 三字段（票 03 契约不动）；
结果帧追加 ok/duration_ms/tokens_in/tokens_out；tool 子事件帧＝
node/kind/tool/ok/duration_ms（explore 与 execute 内真实子步骤）。三型形状在
tests/test_on_event.py 键集钉死，本文件钉传输面：happy 全序列（含票 06 新字段
原样过 SSE 不丢键）＋自纠错故事在帧流里可见（含执行失败红点胶囊）＋前置拒绝与
/api/ask 同序同文案（拒在起流与调模型前）。末帧 event:answer ＝ /api/ask 契约本体
（含票 04 chart 字段；同源 answer_to_payload，两端点不得漂移——**票 06 末帧零动**
亦在此钉）。在途锁＝agent 级共用一把：门控假 LLM＋双 TestClient 同 app 模拟并发。
"""
import json
import threading

import pytest
from fastapi.testclient import TestClient

from qadata.web.agents import AgentStore
from qadata.web.app import create_app
from tests.fakes import ScriptedLLM
from tests.test_web_api import (
    _CONTRACT_KEYS,
    _HAPPY_SCRIPT,
    _S,
    _agent_with_datasource,
)

_BAD_SQL = "SELECT nope FROM students"
_GOOD_SQL = "SELECT name FROM students WHERE id = 2"
_RETRY_SCRIPT = ["改写", _BAD_SQL, _GOOD_SQL, "Bob 数学 88 分"]


@pytest.fixture
def store(tmp_path):
    return AgentStore(tmp_path / "agents", metrics_dir=tmp_path / "metrics")


def _app(llm, store):
    return create_app(llm=llm, settings=_S, agents=store,
                      static_dir="__no_such_dist_for_tests__")


def _client(llm, store) -> TestClient:
    return TestClient(_app(llm, store))


def _parse(text: str) -> list[tuple[str | None, dict]]:
    """SSE 体 → [(event, data)]——app.py 产出格式的解析对偶。"""
    frames = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        event = data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[len("event: "):]
            elif line.startswith("data: "):
                data = json.loads(line[len("data: "):])
        assert data is not None, f"帧缺 data 行：{block!r}"
        frames.append((event, data))
    return frames


def _progress(frames):
    return [d for ev, d in frames if ev is None]


def _steps(frames):
    """非 tool 的进度帧（start＋结果），序列故事断言用；tool 子事件另测。"""
    return [f for f in _progress(frames) if f.get("kind") != "tool"]


def _strip_ms(frames):
    """剥 duration_ms（计时量防抖），并断言其只在结果/tool 帧上在场。"""
    out = []
    for f in frames:
        g = dict(f)
        has = g.pop("duration_ms", None) is not None
        assert has == (g.get("kind") == "tool" or g["status"] != "start"), f
        out.append(g)
    return out


# ── 帧形状与末帧契约 ────────────────────────────────────────────────


def test_stream_happy_frames_and_answer_contract(store, fixture_db):
    a = _agent_with_datasource(store, fixture_db)
    llm = ScriptedLLM(_HAPPY_SCRIPT)
    res = _client(llm, store).post(
        "/api/ask/stream", json={"agent_id": a.id, "question": "Bob 成绩如何"})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/event-stream")
    frames = _parse(res.text)
    assert frames[-1][0] == "answer"
    assert set(frames[-1][1]) == _CONTRACT_KEYS  # 末帧即 14 字段契约（含票 03 clarification），一字节不多不少
    assert frames[-1][1]["failed"] is False and llm.calls == 3
    # 票 06 传输面：结果帧带 ok/tokens（duration_ms 由 _strip_ms 验在场后剥除）、
    # explore/execute 的 tool 子事件帧原样过流不丢键，顺序即真实发生顺序
    def rf(node, attempt, status, ok=True):
        return {"node": node, "attempt": attempt, "status": status,
                "ok": ok, "tokens_in": 0, "tokens_out": 0}

    def tf(node, tool, ok=True):
        return {"node": node, "kind": "tool", "tool": tool, "ok": ok}

    assert _strip_ms(_progress(frames)) == [
        {"node": "understand", "attempt": 0, "status": "start"},
        rf("understand", 0, "解析失败，按原问题作答"),
        {"node": "explore", "attempt": 0, "status": "start"},
        tf("explore", "list_tables"),
        tf("explore", "get_schema"),
        rf("explore", 0, "取到 Schema"),
        {"node": "generate", "attempt": 0, "status": "start"},
        rf("generate", 0, "生成 SQL"),
        {"node": "execute", "attempt": 0, "status": "start"},
        tf("execute", "execute_sql"),
        rf("execute", 1, "执行成功：1 行"),
        {"node": "verify", "attempt": 1, "status": "start"},
        rf("verify", 1, "校验通过"),
        {"node": "respond", "attempt": 1, "status": "start"},
        rf("respond", 1, "作答完成"),
    ]


def test_stream_shows_self_correction_story(store, fixture_db):
    """传输面可见自纠错：一次执行失败（含中文修复建议）→ 再 generate → 成功。"""
    a = _agent_with_datasource(store, fixture_db)
    res = _client(ScriptedLLM(_RETRY_SCRIPT), store).post(
        "/api/ask/stream", json={"agent_id": a.id, "question": "谁成绩最好"})
    parsed = _parse(res.text)
    prog = _steps(parsed)
    assert [f["node"] for f in prog] == [
        "understand", "understand", "explore", "explore",
        "generate", "generate", "execute", "execute",
        "generate", "generate", "execute", "execute",
        "verify", "verify", "respond", "respond",
    ]
    failed = [f for f in prog if f["status"].startswith("执行失败")]
    assert len(failed) == 1 and "修复建议" in failed[0]["status"]
    assert failed[0]["ok"] is False  # 票 06：失败步结果帧红标记
    assert [f["attempt"] for f in prog][-2:] == [2, 2]  # 重试环上 attempt 单调推进
    # 票 06 红点胶囊在流里可见：一次 execute_sql 失败帧＋一次成功帧（各归其 execute 步）
    chips = [f for f in _progress(parsed) if f.get("kind") == "tool"
             and f["tool"] == "execute_sql"]
    assert [c["ok"] for c in chips] == [False, True]


def test_stream_answer_matches_blocking_endpoint(store, fixture_db):
    """末帧与 /api/ask 同源不漂移：同脚本两跑，answer 帧体＝阻塞端点响应体。"""
    a = _agent_with_datasource(store, fixture_db)
    body = {"agent_id": a.id, "question": "Bob 成绩如何"}
    blocking = _client(ScriptedLLM(_HAPPY_SCRIPT), store).post("/api/ask", json=body).json()
    stream = _client(ScriptedLLM(_HAPPY_SCRIPT), store).post("/api/ask/stream", json=body)
    ev, data = _parse(stream.text)[-1]
    assert ev == "answer"
    assert data == blocking


def test_stream_answer_carries_chart_field(store, fixture_db):
    """票 04：chart 非 null 路径也要钉在流上（happy 全序列走的是文本单格＝null 形）——
    两端点同源不漂移测试只证相等，此测钉"相等且真有值"。"""
    a = _agent_with_datasource(store, fixture_db)
    llm = ScriptedLLM(["改写", "SELECT COUNT(*) AS n FROM students", "共 2 人"])
    res = _client(llm, store).post(
        "/api/ask/stream", json={"agent_id": a.id, "question": "有几名学生"})
    ev, data = _parse(res.text)[-1]
    assert ev == "answer"
    assert data["chart"] == {"type": "number", "x": None, "series": [0]}
    assert llm.calls == 3


# ── 前置拒绝：与 /api/ask 同序同文案，拒在起流与调模型前 ────────────


def test_stream_rejects_before_llm(store, fixture_db):
    llm = ScriptedLLM(_HAPPY_SCRIPT)
    client = _client(llm, store)
    empty = store.create("空壳", "")
    res = client.post("/api/ask/stream", json={"agent_id": "deadbeef0000", "question": "题"})
    assert res.status_code == 404  # JSON 错误而非流
    res = client.post("/api/ask/stream", json={"agent_id": empty.id, "question": "题"})
    assert res.status_code == 400 and "未配置数据源" in res.json()["detail"]
    assert client.post("/api/ask/stream",
                       json={"agent_id": empty.id, "question": ""}).status_code == 422
    assert llm.calls == 0  # 全部拒在起流与调模型之前


def test_stream_unknown_agent_no_frames(store):
    """404 响应不得以 text/event-stream 形态伪装成流。"""
    res = _client(None, store).post("/api/ask/stream",
                                    json={"agent_id": "deadbeef0000", "question": "题"})
    assert "text/event-stream" not in res.headers["content-type"]
    assert res.json()["detail"]


# ── 在途锁：流式在途拒一切并发，收口即放行 ─────────────────────────


class _GatedLLM(ScriptedLLM):
    """第一次 invoke 前卡在门上——测试据此构造"在途"窗口（不依赖真实时延）。"""

    def __init__(self, responses):
        super().__init__(responses)
        self.entered = threading.Event()
        self.gate = threading.Event()

    def invoke(self, prompt):
        if self.calls == 0:
            self.entered.set()
            assert self.gate.wait(15), "门未被释放，测试结构失效"
        return super().invoke(prompt)


def test_inflight_lock_rejects_and_releases(store, fixture_db):
    a = _agent_with_datasource(store, fixture_db)
    body = {"agent_id": a.id, "question": "Bob 成绩如何"}
    gated = _GatedLLM(_HAPPY_SCRIPT * 2)  # 两题的脚本：持锁题＋验证放行的第二题
    app = _app(gated, store)
    held: list = []  # 持锁请求的 Response（httpx.Response）
    holder = threading.Thread(
        target=lambda: held.append(
            TestClient(app).post("/api/ask/stream", json=body)))
    holder.start()
    try:
        assert gated.entered.wait(5)  # 流式题已在途（锁持有中）
        other = TestClient(app)
        res = other.post("/api/ask", json=body)
        assert res.status_code == 409 and "正在回答上一个问题" in res.json()["detail"]
        res = other.post("/api/ask/stream", json=body)
        assert res.status_code == 409  # 两端点共用一把
        assert gated.calls == 0  # 被拒请求没烧模型
    finally:
        gated.gate.set()
    holder.join(20)
    assert not holder.is_alive() and held[0].status_code == 200
    assert _parse(held[0].text)[-1][0] == "answer"
    assert gated.calls == 3
    # 锁随流收口释放：同 app 第二题畅通（脚本续用，calls→6）
    res = TestClient(app).post("/api/ask", json=body)
    assert res.status_code == 200
    assert res.json()["failed"] is False
    assert gated.calls == 6


def test_inflight_lock_released_on_blocking_failure(store, fixture_db):
    """失败答案（200）同样放锁：下一题不被幽灵持锁饿死。"""
    a = _agent_with_datasource(store, fixture_db)
    client = _client(ScriptedLLM(["改写", _BAD_SQL, _BAD_SQL, _BAD_SQL]), store)
    first = client.post("/api/ask", json={"agent_id": a.id, "question": "谁成绩最好"})
    assert first.status_code == 200 and first.json()["failed"] is True
    second = client.post("/api/ask/stream",
                         json={"agent_id": a.id, "question": "再试"})
    assert second.status_code == 200  # 非 409＝锁已释放
