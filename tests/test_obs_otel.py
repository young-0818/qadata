"""M9 票 01 专测：OTel 观测骨架——on_event 帧流单源镜像为 span 树（内存 exporter，零联网）。

钉五件事（票面验收全表）：
① span 树形状：假模型跑一问，一问＝一条 trace，节点＝root 之子 span、tool 帧＝其孙
  （execute_sql 挂 execute 之下）、重试两代同名 span 各挂 attempt、串联键/token/ok/耗时随帧落上；
② 默认关态双跑＝逐字节一致姊妹钉（审计账本字节、SSE 帧序列、prompt 三者全不动）；
③ noop 降级：开关开但 SDK provider 未装（全局＝NoOp）时 NonRecordingSpan 全吞、零联网零异常；
④ thinking 帧不入 span（Q9 裁决：直播安慰剂）；obs-only 镜像不开流式旁路（观测不改变
  LLM 调用形态——eval 配对轮与历史可比的前提，stream_used==0 姊妹形）；
⑤ 三入口串联键就位：eval 挂 run_id＋question_id、web 挂 agent_id＋session_id＋轮 ts。
内存 provider 一律测试本地构造（trace.set_tracer_provider 全局 first-set-wins，不共享）。
"""
import json
import re
import sqlite3
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from qadata.config import Settings
from qadata.graph.build import run_question
from qadata.llm.tracing import TraceLogger
from qadata.obs import Obs, obs_for
from qadata.web.agents import AgentStore
from qadata.web.app import create_app
from tests.fakes import ScriptedLLM

_S = Settings(api_key="", base_url="", model="test-model", retry_budget=3)
_GOOD = "SELECT name FROM students WHERE id = 2"
_BAD = "SELECT nope FROM students"
_RETRY_SCRIPT = ["改写", _BAD, _GOOD, "Bob 数学 88 分"]
_HAPPY = ['{"question":"查成绩"}', _GOOD, "Bob 88"]
_ATTRS = {"agent_id": "a1b2c3d4e5f6", "session_id": "s1",
          "turn_ts": "2026-09-18T10:00:00+08:00"}  # 与轮档 ts 同式（sessions.turn_ts 唯一式）


def _mem():
    """测试本地 provider（不碰全局）＋内存 exporter。返回 (tracer, 收集器)。"""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider.get_tracer("test"), exporter


def _run_with_obs(db, script, usage=None, live=False, attrs=None):
    """跑一问：obs 镜像进内存 exporter；live＝另挂直播消费者（SSE 同位）。"""
    tracer, exporter = _mem()
    obs = Obs(tracer, "谁成绩最好", attrs if attrs is not None else _ATTRS)
    frames: list[dict] = []
    llm = ScriptedLLM(script, usage=usage)
    answer = run_question(db, "谁成绩最好", llm=llm, settings=_S, obs=obs,
                          on_event=frames.append if live else None)
    return exporter.get_finished_spans(), frames, llm, answer


def _answer_dump(ans) -> str:
    """答案可比形态：elapsed_ms 是计时量不比（test_off_state_answer_identical 同规）。"""
    d = dict(vars(ans))
    if d.get("result") is not None:
        d["result"] = {k: v for k, v in vars(d["result"]).items() if k != "elapsed_ms"}
    return json.dumps(d, ensure_ascii=False, sort_keys=True, default=str)


# ── ① span 树形状 ──────────────────────────────────────────────────


def test_span_tree_shape_and_correlation(fixture_db):
    spans = _run_with_obs(fixture_db, _RETRY_SCRIPT)[0]
    by_name: dict[str, list] = {}
    for s in spans:
        by_name.setdefault(s.name, []).append(s)
    root = by_name["谁成绩最好"][0]
    assert root.parent is None  # 一问＝一条 trace 的根
    assert dict(root.attributes) == _ATTRS  # 串联键挂 root（web 形：agent+session+轮 ts）
    # 每个 span 都带串联键（Langfuse 全 span 传播要求），且同一条 trace
    for s in spans:
        assert s.context.trace_id == root.context.trace_id
        assert dict(s.attributes)["session_id"] == "s1"
    # 节点 span＝root 之子；重试两代同名各一条，attempt 落属性
    gens = sorted(by_name["generate"], key=lambda s: dict(s.attributes)["attempt"])
    assert [dict(s.attributes)["attempt"] for s in gens] == [0, 1]
    for s in gens:
        assert s.parent.span_id == root.context.span_id
    # tool 帧＝其孙：execute_sql 两枚挂两代 execute 之下（红绿各一）
    execs = by_name["execute"]
    tools = by_name["execute_sql"]
    assert len(tools) == 2
    assert {t.parent.span_id for t in tools} == {e.context.span_id for e in execs}
    assert sorted(t.attributes["ok"] for t in tools) == [False, True]
    red = next(t for t in tools if t.attributes["ok"] is False)
    assert red.status.status_code.name == "ERROR"  # 失败胶囊红点＝红态


def test_frame_values_land_on_spans(fixture_db):
    usage = {"input_tokens": 100, "output_tokens": 5, "total_tokens": 105}
    spans, _, llm, answer = _run_with_obs(fixture_db, _RETRY_SCRIPT, usage=usage)
    assert answer.failed is False and llm.calls == 4
    und = [s for s in spans if s.name == "understand" and s.attributes.get("status")]
    assert und, "understand 结果 span 在场"
    a = dict(und[0].attributes)
    assert a["status"] == "解析失败，按原问题作答" and a["ok"] is True
    assert (a["tokens_in"], a["tokens_out"]) == (100, 5)  # token 随帧落上
    assert a["gen_ai.usage.input_tokens"] == 100  # Langfuse 界面 token 计数只认语义约定
    assert isinstance(a["duration_ms"], int) and a["duration_ms"] >= 0
    # 失败收口＝ERROR 红态（execute 第一代）
    bad_exec = [s for s in spans if s.name == "execute" and s.attributes.get("ok") is False]
    assert bad_exec and bad_exec[0].status.status_code.name == "ERROR"


def test_thinking_frame_not_mirrored():
    tracer, exporter = _mem()
    obs = Obs(tracer, "q", {})
    obs.mirror(None)({"node": "understand", "kind": "thinking", "text": "嗯……"})
    obs.close()
    assert [s.name for s in exporter.get_finished_spans()] == ["q"]  # 只有 root，零灌入


def test_dangling_node_span_closed_on_pause():
    """HITL interrupt 暂停形态（start 帧后节点重抛、无结果帧）：close() 兜住孤儿 span 不悬空。"""
    tracer, exporter = _mem()
    obs = Obs(tracer, "q", {})
    cb = obs.mirror(None)
    cb({"node": "understand", "attempt": 0, "status": "start"})
    obs.close()
    obs.close()  # 幂等（run_question finally 与调用方双路只生效一次）
    names = sorted(s.name for s in exporter.get_finished_spans())
    assert names == ["q", "understand"]


# ── ④ 观测不改变调用形态 ───────────────────────────────────────────


def test_obs_only_keeps_invoke_path(fixture_db):
    """无直播消费者（eval/CLI 开态）＝invoke 调用形态原样——配对轮与历史可比的命门。"""
    spans, frames, llm, answer = _run_with_obs(fixture_db, _RETRY_SCRIPT)
    assert llm.stream_used == 0 and answer.failed is False
    assert frames == []  # 帧没被凭空造出直播副本
    assert len(spans) > 1  # 对照组：观测镜像真收到了帧
    # 有直播消费者时流式照旧（thinking 旁路不因 obs 挂接而被关回去）：understand＋generate×2
    _, fr2, llm2, _ = _run_with_obs(fixture_db, _RETRY_SCRIPT, live=True)
    assert llm2.stream_used == 3 and len(fr2) > 0


# ── ② 默认关双跑＝逐字节一致姊妹钉 ─────────────────────────────────


def test_default_off_double_run_byte_identical(fixture_db, tmp_path, monkeypatch):
    assert obs_for(_S, "q", {}) is None  # 开关缺省＝零 Obs，挂接面不存在
    monkeypatch.setattr("qadata.llm.tracing.now_beijing", lambda: "2026-09-18 00:00:00")
    runs = []
    for i in range(2):
        frames: list[dict] = []
        llm = ScriptedLLM(_RETRY_SCRIPT)
        tracer = TraceLogger(tmp_path / f"t{i}.jsonl", run_id="runX")
        # obs 参数在场但为 None（关态调用面）——与不带该参数的历史调用逐行为一致
        answer = run_question(fixture_db, "谁成绩最好", llm=llm, settings=_S, tracer=tracer,
                              on_event=frames.append, obs=obs_for(_S, "q", {}))
        stripped = [{k: v for k, v in f.items() if k != "duration_ms"} for f in frames]
        # 账本钉＝字段与内容逐字节（计时量 latency_s 与耗时同源，同 elapsed_ms 规矩剥掉再比）
        ledger = re.sub(rb'"latency_s": [0-9.]+', b'"latency_s": L',
                        (tmp_path / f"t{i}.jsonl").read_bytes())
        runs.append((json.dumps(stripped, ensure_ascii=False, sort_keys=True),
                     llm.prompts, ledger, _answer_dump(answer)))
    assert runs[0][0] == runs[1][0]      # SSE 帧序列一致
    assert runs[0][1] == runs[1][1]      # prompt 一致
    assert runs[0][2] == runs[1][2]      # 审计账本一致（run_id/ts 已同值钉）
    assert runs[0][3] == runs[1][3]      # 答案一致


# ── ③ noop 降级（开关开但出口未装＝DataAgent 同款姿势）────────────


def test_noop_downgrade_zero_network(fixture_db, monkeypatch):
    installs: list = []
    monkeypatch.setattr("qadata.obs.install", lambda s: installs.append(s))
    st = replace(_S, otel_enabled=True)
    obs = obs_for(st, "谁成绩最好", {"run_id": "r1"})
    assert obs is not None and installs == [st]  # 开态装出口恰一次
    llm = ScriptedLLM(_RETRY_SCRIPT)
    answer = run_question(fixture_db, "谁成绩最好", llm=llm, settings=st, obs=obs)
    assert answer.failed is False  # 全局 NoOp provider＝NonRecordingSpan 全吞：零联网零异常


def test_obs_frame_drop_does_not_harm_answer(fixture_db, monkeypatch):
    """观测内部炸（如坏 exporter）不连累本体：丢 span 记数，答案与直播照跑（值采样先例姿势）。"""
    tracer, _ = _mem()
    obs = Obs(tracer, "q", {})
    monkeypatch.setattr(obs, "_frame", lambda f: (_ for _ in ()).throw(RuntimeError("boom")))
    frames: list[dict] = []
    llm = ScriptedLLM(_RETRY_SCRIPT)
    answer = run_question(fixture_db, "谁成绩最好", llm=llm, settings=_S, obs=obs,
                          on_event=frames.append)
    assert answer.failed is False and len(frames) > 0
    assert obs._dropped == len(frames)


# ── ⑤ 三入口串联键就位 ─────────────────────────────────────────────


def test_eval_run_one_attaches_correlation(tmp_path, monkeypatch):
    from qadata.eval import bird

    seen: list[dict] = []
    monkeypatch.setattr(bird, "obs_for", lambda s, name, attrs: seen.append(attrs) or None)
    db = tmp_path / "school" / "school.sqlite"
    db.parent.mkdir()
    conn = sqlite3.connect(db)
    conn.executescript("CREATE TABLE students (id INTEGER PRIMARY KEY, name TEXT, grade INTEGER);"
                       "INSERT INTO students VALUES (2, 'Bob', 2);")
    conn.commit()
    conn.close()
    q = {"question_id": 7, "db_id": "school", "question": "谁成绩最好",
         "SQL": "SELECT name FROM students", "difficulty": "simple"}
    tracer = TraceLogger(tmp_path / "traces.jsonl", run_id="r9")
    rec = bird._run_one(q, str(tmp_path), ScriptedLLM(_RETRY_SCRIPT), 50, tracer)
    assert rec["correct"] is True
    assert seen == [{"run_id": "r9", "question_id": "7"}]


def test_web_ask_attaches_correlation(tmp_path, fixture_db, monkeypatch):
    from qadata.web import app as webapp

    seen: list[dict] = []
    monkeypatch.setattr(webapp, "obs_for", lambda s, name, attrs: seen.append(attrs) or None)
    store = AgentStore(tmp_path / "agents", metrics_dir=tmp_path / "metrics")
    a = store.create("试验智能体", "观测契约用")
    with open(fixture_db, "rb") as f:
        store.store_datasource(a.id, f.read(), "school.sqlite")
    client = TestClient(create_app(llm=ScriptedLLM(_HAPPY), settings=_S, agents=store,
                                   static_dir=Path("__no_such_dist_for_tests__")))
    res = client.post("/api/ask", json={"agent_id": a.id, "question": "谁成绩最好",
                                        "session_id": "abc123def456"})
    assert res.status_code == 200
    assert seen[0]["agent_id"] == a.id and seen[0]["session_id"] == "abc123def456"
    assert seen[0]["langfuse.session.id"] == "abc123def456"  # Langfuse Sessions 视图只认这个字面
    # turn_ts 与轮档/feedback 锚点同式（isoformat 秒级）——只钉形不钉值（防跨秒抖动）
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2}",
                        seen[0]["turn_ts"])


def test_cli_ask_self_makes_run_id_trace(fixture_db, tmp_path, monkeypatch):
    """三入口之 CLI ask＝run_question 内部自造一条（调用面零参数改动钉）：
    开态缺省 obs 时按 question＋run_id 生成镜像；关态缺省＝零挂接。"""
    from qadata.graph import build

    seen: list[tuple] = []
    monkeypatch.setattr(build, "obs_for",
                        lambda s, name, attrs: seen.append((name, attrs)) or None)
    tracer = TraceLogger(tmp_path / "t.jsonl", run_id="r7")
    run_question(fixture_db, "谁成绩最好", llm=ScriptedLLM(_RETRY_SCRIPT), settings=_S,
                 tracer=tracer)
    assert seen == [("谁成绩最好", {"run_id": "r7"})]  # 关态也照查＝闸在 obs_for 内，零染指
