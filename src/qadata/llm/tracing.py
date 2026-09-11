"""节点级 tracing：JSONL 追加记录 token/延迟（自建，不用 LangSmith）。

票 10 显示口径：ts 裸北京时间（UTC+8，秒级，无偏移标记——中国无夏令时）；
延迟以秒计（latency_s，浮点两位）。历史行的 UTC ts/latency_ms 不迁移，
按各行的字段名/偏移各自解读；人读的聚合界面是 runs/tokens-<run_id>.json。
"""
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from qadata.llm.gateway import invoke_with_backoff

BEIJING = timezone(timedelta(hours=8))

# 节点级 trace 落盘路径（单一真源：ask/eval/serve 共用；仓库根 cwd 约定与 metrics_dir 同款）
TRACE_PATH = "runs/traces.jsonl"

_ZERO_USAGE = {"llm_calls": 0, "input_tokens": 0, "output_tokens": 0,
               "total_tokens": 0, "latency_s": 0.0}

# timed_invoke→log 的显式契约：带 token 字段的 log 才计为一次 LLM 调用
# （纯计时类 log 不计）。改字段名/加字段时与 extract_usage 同步。
_LLM_LOG_MARKERS = ("input_tokens", "output_tokens")
_USAGE_SUM_KEYS = ("input_tokens", "output_tokens", "total_tokens")


def now_beijing() -> str:
    """裸北京时间戳（机器可按 UTC+8 语义解析，人眼直读）。"""
    return datetime.now(BEIJING).strftime("%Y-%m-%d %H:%M:%S")


class TraceLogger:
    def __init__(self, path: str | Path, run_id: str | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self._context: dict = {}
        # 票 10：进程内按 question_id 累计 LLM 调用账（token/延迟/次数），
        # 供评测器逐题落字段；不落盘、不跨进程——并发模式每题独占一个实例。
        self._usage: dict[str, dict] = {}

    def set_context(self, **kw) -> None:
        """合并上下文（如 question_id）；其后所有记录自动携带。"""
        self._context.update(kw)

    def log(self, node: str, **payload) -> None:
        rec = {"ts": now_beijing(), "run_id": self.run_id,
               **self._context, "node": node, **payload}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if any(k in payload for k in _LLM_LOG_MARKERS):
            key = str(self._context.get("question_id", "-"))
            agg = self._usage.setdefault(key, dict(_ZERO_USAGE))
            agg["llm_calls"] += 1
            for k in _USAGE_SUM_KEYS:
                agg[k] += int(payload.get(k) or 0)
            agg["latency_s"] = round(agg["latency_s"] + float(payload.get("latency_s") or 0.0), 2)

    def usage_for(self, question_id) -> dict:
        """取该题累计（不重置）；没跑过返回全 0。"""
        return dict(self._usage.get(str(question_id), _ZERO_USAGE))


def extract_usage(message) -> dict:
    """从 AIMessage 提取 usage；缺失或字段为 null 时全 0（fake/旧版模型无此字段，供应商可能回 null）。"""
    u = getattr(message, "usage_metadata", None) or {}
    return {
        "input_tokens": int(u.get("input_tokens") or 0),
        "output_tokens": int(u.get("output_tokens") or 0),
        "total_tokens": int(u.get("total_tokens") or 0),
    }


def timed_invoke(llm, prompt: str, node: str, tracer: TraceLogger | None, limiter=None) -> str:
    """调用 LLM（带指数退避）并记录该节点的 token 与延迟。返回 response.content。"""
    t0 = time.perf_counter()
    resp = invoke_with_backoff(llm, prompt, limiter=limiter)
    latency = round(time.perf_counter() - t0, 2)
    if tracer is not None:
        usage = extract_usage(resp)
        tracer.log(node, latency_s=latency, **usage)
    return resp.content
