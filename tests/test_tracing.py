import json

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
    log.log("generate", input_tokens=10, output_tokens=5, latency_ms=120)
    log.log("execute", latency_ms=3)
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
    assert rec["node"] == "understand" and "latency_ms" in rec
