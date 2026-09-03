"""节点级 tracing：JSONL 追加记录 token/延迟（自建，不用 LangSmith）。"""
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from qadata.llm.gateway import invoke_with_backoff


class TraceLogger:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, node: str, **payload) -> None:
        rec = {"ts": datetime.now(timezone.utc).isoformat(), "node": node, **payload}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def extract_usage(message) -> dict:
    """从 AIMessage 提取 usage；缺失或字段为 null 时全 0（fake/旧版模型无此字段，供应商可能回 null）。"""
    u = getattr(message, "usage_metadata", None) or {}
    return {
        "input_tokens": int(u.get("input_tokens") or 0),
        "output_tokens": int(u.get("output_tokens") or 0),
        "total_tokens": int(u.get("total_tokens") or 0),
    }


def timed_invoke(llm, prompt: str, node: str, tracer: TraceLogger | None) -> str:
    """调用 LLM（带指数退避）并记录该节点的 token 与延迟。返回 response.content。"""
    t0 = time.perf_counter()
    resp = invoke_with_backoff(llm, prompt)
    latency = int((time.perf_counter() - t0) * 1000)
    if tracer is not None:
        usage = extract_usage(resp)
        tracer.log(node, latency_ms=latency, **usage)
    return resp.content
