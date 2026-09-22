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

from qadata.llm.gateway import _is_retryable, backoff_delay, invoke_with_backoff

BEIJING = timezone(timedelta(hours=8))

# 节点级 trace 落盘路径（单一真源：ask/eval/serve 共用；仓库根 cwd 约定与 DEFAULT_INDEX_DIR 同款）
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


def tool_start_frame(node: str, name: str) -> dict:
    """tool start 帧（实时工具链）：慢操作开跑的活口标记——胶囊当场出现转圈，
    收口帧（tool_frame）形状一字不动、原位翻终态。配对规则＝前端按「同 node
    同 tool 最近一个未收口行」缝合，不引入 call-id（单请求内帧流有序串行）。
    仅对有计时区间的调用发；budget_fuse 类即时入账帧（无"跑"的区间）不发。"""
    return {"node": node, "kind": "tool", "tool": name, "status": "start"}


def tool_frame(node: str, name: str, t0: float, ok: bool = True,
               detail: str | None = None) -> dict:
    """M8 票 06 tool 子事件帧形状唯一源（必需 {node,kind,tool,ok,duration_ms}）。
    帧形是契约面——graph（execute/respond）与 tools（explore 子步骤）两处发、
    前端联合类型按此收窄，形状绝不许漂移，故收敛于此（compose_supplement 单源先例）。
    t0 为 time.perf_counter() 起点，duration_ms 就地算。
    detail＝可选尾字段（chart 可选字段同族，旧消费者忽略即得）：值链「实际取值」用它带
    命中明细（列→实际存储值），让胶囊显示"揪出了什么"而非只有一个耗时。"""
    frame = {"node": node, "kind": "tool", "tool": name, "ok": ok,
             "duration_ms": round((time.perf_counter() - t0) * 1000)}
    if detail:
        frame["detail"] = detail
    return frame


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


def _log_node(tracer: TraceLogger | None, sink: dict | None, node: str,
              usage: dict, latency: float) -> None:
    """节点收口的两处落点（tracer 账本＋当前步 token 累计）——timed_invoke 与
    timed_stream 共用，防两份字面漂移（tool_frame／compose_supplement 单源先例）。"""
    if tracer is not None:
        tracer.log(node, latency_s=latency, **usage)
    if sink is not None:
        sink["tin"] += usage["input_tokens"]
        sink["tout"] += usage["output_tokens"]


def timed_invoke(llm, prompt: str, node: str, tracer: TraceLogger | None, limiter=None,
                 sink: dict | None = None) -> str:
    """调用 LLM（带指数退避）并记录该节点的 token 与延迟。返回 response.content。
    sink（M8 票 06）＝{"tin","tout"} 累计 dict：逐调用 token 旁路进当前步的进度帧
    （费用/ token 卡的数据源）；缺省 None 与现状逐行为一致（CLI/eval 零染指）。"""
    t0 = time.perf_counter()
    resp = invoke_with_backoff(llm, prompt, limiter=limiter)
    latency = round(time.perf_counter() - t0, 2)
    if tracer is not None or sink is not None:
        _log_node(tracer, sink, node, extract_usage(resp), latency)
    return resp.content


_MAX_STREAM_RETRIES = 1  # 与 invoke_with_backoff 同额度（重试次数不是配置面，别处不变）


def timed_stream(llm, prompt: str, node: str, tracer: TraceLogger | None, limiter=None,
                 sink: dict | None = None, on_event=None, max_thinking: int = 2000,
                 sleep=time.sleep) -> str:
    """流式旁路（M8 票 08）：`timed_invoke` 的流式对偶——把 12~30s 死寂转圈变成「看着它想」。

    逐 chunk 把 `reasoning_content` 增量转成 thinking 帧 `{node, kind, text}` 推 on_event
    （单流累计截断 ≤max_thinking 字防灌 DOM）；收口经 `_log_node` 落成**与 timed_invoke 同形**
    的 tracer 记录（账本形态不变、零新增调用）＋返回完整 content（同签名同返回）。

    退避窄化：仅**首 token 前**的连接错重开流（此时一帧未发，重连无重复——复用 gateway
    的 _is_retryable 判据＋ backoff_delay 同一支退避曲线）；首 token 后断流＝如实上抛
    （thinking 帧不可回收，重播会灌前端——SSE 诚实中断先例）。

    仅 on_event 在场才该调本函数（节点侧以 on_event 分流走本路）；CLI/eval 恒走 timed_invoke，
    调用面零变化（关态源码扫描姊妹钉，on_event 纪律第三例）。"""
    t0 = time.perf_counter()
    for attempt in range(_MAX_STREAM_RETRIES + 1):
        yielded = False  # 首 token 门：收到任一 chunk 后不再重连
        try:
            if limiter is not None:
                limiter.acquire()
            parts: list[str] = []
            thinking_len = 0
            usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
            for chunk in llm.stream(prompt):
                yielded = True
                rc = (getattr(chunk, "additional_kwargs", None) or {}).get("reasoning_content")
                if rc and on_event is not None and thinking_len < max_thinking:
                    take = rc[: max_thinking - thinking_len]
                    thinking_len += len(take)
                    on_event({"node": node, "kind": "thinking", "text": take})
                if chunk.content:
                    parts.append(chunk.content)
                if getattr(chunk, "usage_metadata", None):
                    usage = extract_usage(chunk)
            if tracer is not None or sink is not None:
                _log_node(tracer, sink, node, usage, round(time.perf_counter() - t0, 2))
            return "".join(parts)
        except Exception as e:
            if yielded or attempt >= _MAX_STREAM_RETRIES or not _is_retryable(e):
                raise
            sleep(backoff_delay(attempt))
    raise AssertionError("timed_stream 循环不可达终点")  # 兜编译器/防御：try 必 return 或 except 必 raise
