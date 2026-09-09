import json
import re

from qadata.llm.tracing import TraceLogger, extract_usage, timed_invoke


class FakeMsg:
    def __init__(self, content, usage=None):
        self.content = content
        self.usage_metadata = usage


class FakeLLM:
    def __init__(self, msg):
        self.msg = msg
        self.calls = 0

    def invoke(self, _prompt):
        self.calls += 1
        return self.msg


def test_trace_logger_writes_jsonl(tmp_path):
    p = tmp_path / "t.jsonl"
    log = TraceLogger(p)
    log.log("generate", input_tokens=10, output_tokens=5, latency_s=0.12)
    log.log("execute", latency_s=0.003)
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    rec = json.loads(lines[0])
    assert rec["node"] == "generate" and rec["input_tokens"] == 10 and "ts" in rec


def test_extract_usage_present_and_absent():
    full = extract_usage(FakeMsg("x", {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}))
    assert full == {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}
    empty = extract_usage(FakeMsg("x"))
    assert empty == {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def test_timed_invoke_returns_content_and_logs(tmp_path):
    log = TraceLogger(tmp_path / "t.jsonl")
    out = timed_invoke(FakeLLM(FakeMsg("hello")), "prompt", "understand", log)
    assert out == "hello"
    rec = json.loads((tmp_path / "t.jsonl").read_text(encoding="utf-8"))
    assert rec["node"] == "understand" and "latency_s" in rec
    # 票 10 显示口径：ts 裸北京时间（秒级）、延迟以秒计——人眼直读，不再 UTC 反算
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", rec["ts"])
    assert isinstance(rec["latency_s"], float)


def test_timed_invoke_without_tracer_returns_content():
    # tracer=None 分支是 Task 7 的契约之一：不记录但必须正常返回 content
    out = timed_invoke(FakeLLM(FakeMsg("hi")), "prompt", "generate", None)
    assert out == "hi"


def test_trace_logger_run_id_auto_and_context(tmp_path):
    log = TraceLogger(tmp_path / "t.jsonl")
    assert len(log.run_id) == 12
    log.set_context(question_id="42")
    log.log("generate", latency_s=0.005)
    rec = json.loads((tmp_path / "t.jsonl").read_text(encoding="utf-8"))
    assert rec["run_id"] == log.run_id and rec["question_id"] == "42"


def test_trace_logger_explicit_run_id(tmp_path):
    assert TraceLogger(tmp_path / "t.jsonl", run_id="fixed").run_id == "fixed"


def test_usage_for_aggregates_per_question(tmp_path):
    """带 token 字段的 log 记为一次 LLM 调用，按上下文 question_id 归账；互不串账。"""
    log = TraceLogger(tmp_path / "t.jsonl")
    log.set_context(question_id="42")
    log.log("understand", latency_s=1.5, input_tokens=10, output_tokens=5, total_tokens=15)
    log.log("generate", latency_s=0.5, input_tokens=4, output_tokens=2, total_tokens=6)
    log.set_context(question_id="43")
    log.log("generate", latency_s=9.9, input_tokens=1, output_tokens=1, total_tokens=2)
    assert log.usage_for("42") == {"llm_calls": 2, "input_tokens": 14, "output_tokens": 7,
                                   "total_tokens": 21, "latency_s": 2.0}
    assert log.usage_for("43")["llm_calls"] == 1
    assert log.usage_for("42")["llm_calls"] == 2  # 取数不重置
    assert log.usage_for("未跑过") == {"llm_calls": 0, "input_tokens": 0, "output_tokens": 0,
                                       "total_tokens": 0, "latency_s": 0.0}


def test_usage_for_ignores_non_llm_logs(tmp_path):
    """不带 token 字段的 log（未来若有）不计入调用数。"""
    log = TraceLogger(tmp_path / "t.jsonl")
    log.set_context(question_id="1")
    log.log("execute", latency_s=0.01)
    assert log.usage_for("1")["llm_calls"] == 0
