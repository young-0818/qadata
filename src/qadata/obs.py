"""M9 票 01：OTel 观测骨架——on_event 帧流单源镜像为 span 树，出口可插拔。

裁决链见 .scratch/qadata-m9/spec.md §三（Q8）与 docs/adr/0003：
* **埋点常驻、开关选出口**：帧流（start/result/tool，票 03/06 起本就是 span 流）是唯一埋点，
  本模块只是它的第二个消费者；`Settings.otel_enabled` 默认关＝obs_for 返回 None，
  调用面零挂接、行为逐字节照旧（审计账本 traces.jsonl 与开关无关，原地不动）；
* **一问＝一条 trace**：root span 挂串联键（web＝agent_id＋session_id＋轮 ts，eval＝run_id＋
  question_id），节点步骤＝子 span、tool 帧＝孙 span；token/ok/耗时随帧落上；
* thinking 帧不入 span（Q9 裁决：直播安慰剂，内容已凝结进答案；且单流可数百帧，灌 span 无益）；
* 父子用显式 `set_span_in_context` 挂接、不碰 contextvar attach——eval 并发逐题各持一个 Obs，
  天然零串扰；
* LangSmith 不做主干（Q8 被否），只留 OTLP-sink 插座：想调试 graph 内部时临时给
  install() 里的 provider 多挂一个指向其 OTLP collector 的 span processor 即可（升级判据一行，不立配置面）。

串联键复制进每个 span 的属性（Langfuse 的 OTLP 摄取要求 trace 级属性在所有 span 上重复才可按
session 过滤），gen_ai.usage.* 双写同理由（其界面 token 计数只认这个语义约定）。
"""
import subprocess
import threading
import time

from opentelemetry import trace

# install 的一次性闸（首问触发，与 build_llm 共享实例同款收口）
_installed = False
_install_lock = threading.Lock()
_code_version: str | None = None


def _version() -> str:
    """代码版本＝git 短 sha（跑分轮如实留痕）；无 git 的打包环境退包版本（数字诚实，不猜）。"""
    global _code_version
    if _code_version is None:
        sha = ""
        try:
            sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], check=False,
                                 capture_output=True, text=True, timeout=2).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            sha = ""  # 取版本失败不连累观测出口——退包版本即可
        from importlib.metadata import version
        _code_version = sha or version("qadata")
    return _code_version


def install(settings) -> None:
    """装 OTLP 出口一次：SDK provider＋resource 属性（model/代码版本）＋BatchSpanProcessor。

    端点＝settings.otel_endpoint，空则回落 OTEL_EXPORTER_OTLP_ENDPOINT（Langfuse 自托管
    的鉴权头经 OTEL_EXPORTER_OTLP_HEADERS 标准 env 给，见 .env.example）。
    SDK/exporter 懒 import——默认关进程零碰 OTLP SDK/exporter 代码（otel-api 顶层仅符号表）。"""
    global _installed
    with _install_lock:
        if _installed:
            return
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        provider = TracerProvider(resource=Resource.create({
            "service.name": "qadata",
            "qadata.model": settings.model,
            "qadata.version": _version(),
        }))
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=settings.otel_endpoint or None)))
        trace.set_tracer_provider(provider)
        _installed = True


def obs_for(settings, name: str, attrs: dict) -> "Obs | None":
    """开关闸：关/无 settings＝None（调用面与今日逐行为一致）；开＝装出口后出问级镜像。
    getattr 容忍鸭子类型 settings（评测测试的 SimpleNamespace 无新字段＝按关处理）。"""
    if settings is None or not getattr(settings, "otel_enabled", False):
        return None
    install(settings)
    return Obs(trace.get_tracer("qadata"), name, attrs)


def shutdown() -> None:
    """进程/轮末收口：冲刷批处理缓冲（否则末批 span 随进程丢）。

    未装 SDK（默认关/CI）时全局 provider 无 shutdown＝如实跳过，零联网零异常。"""
    fn = getattr(trace.get_tracer_provider(), "shutdown", None)
    if fn is not None:
        fn()


def _stamp(span, attrs: dict) -> None:
    """帧字段落 span（None 值不收——OTel 属性不许 null）；ok=False＝红（ERROR 态）。"""
    span.set_attributes({k: v for k, v in attrs.items() if v is not None})
    if attrs.get("ok") is False:
        span.set_status(trace.Status(trace.StatusCode.ERROR, str(attrs.get("status") or "")))


class Obs:
    """一问一条 trace：on_event 兼容的帧→span 镜像器（帧型语义钉见 tests/test_on_event.py）。

    生命周期由 run_question/resume_question 收口：构造＝root span 开，mirror()＝取挂接用的
    回调，close()＝兜住一切退出路径（含 HITL interrupt 暂停时未闭合的节点 span）。
    """

    def __init__(self, tracer, name: str, attrs: dict | None = None):
        self._attrs = dict(attrs or {})
        self._root = tracer.start_span(name, attributes=self._attrs)
        self._tracer = tracer
        self._open: dict[str, object] = {}  # 现势节点 span（graph 同步单节点执行，按名一座）
        self._closed = False
        self._dropped = 0

    def mirror(self, on_event):
        """返回包一层的 on_event：帧先镜像成 span，再转原消费者（SSE 等）。

        原消费者缺位（eval/CLI 开态）时打 qadata_no_stream 标记——thinking 流式旁路只该被
        直播消费者开启，观测不得改变 LLM 调用形态（eval 配对轮与历史可比的前提）。"""
        def cb(frame: dict) -> None:
            try:
                self._frame(frame)
            except Exception:  # noqa: BLE001 观测失败不连累本体（值采样先例）：丢 span 记数，答案照跑
                self._dropped += 1
            if on_event is not None:
                on_event(frame)

        # 无直播消费者＝原回调缺位，或其本身已是无流消费者（如 M9 票 02 trail 留痕表）——
        # 标记照传，观测/入档链任何组合都不启流式
        if on_event is None or getattr(on_event, "qadata_no_stream", False):
            cb.qadata_no_stream = True
        return cb

    def _child(self, parent, name: str, attrs: dict, start_time: int | None = None):
        # 串联键复制进每个 span（Langfuse 全 span 传播要求，见模块 docstring）
        return self._tracer.start_span(
            name, context=trace.set_span_in_context(parent),
            attributes={**self._attrs, **attrs}, start_time=start_time)

    def _frame(self, f: dict) -> None:
        kind = f.get("kind")
        node = f.get("node", "")
        if kind == "thinking":
            return  # Q9：不入 span
        if kind == "tool":
            if f.get("status") == "start":
                return  # start 活口帧不建 span（span 一次成型现状零动；Langfuse 里不留半开 span）
            end = time.time_ns()
            attrs = {"node": node, "ok": f["ok"], "duration_ms": f["duration_ms"]}
            span = self._child(self._open.get(node) or self._root, f["tool"], attrs,
                               start_time=end - int(f["duration_ms"] * 1_000_000))
            if not f["ok"]:
                span.set_status(trace.Status(trace.StatusCode.ERROR, f["tool"]))  # 红点同红态
            span.end(end_time=end)
            return
        if f.get("status") == "start":
            self._open[node] = self._child(self._root, node, {"attempt": f["attempt"]})
            return
        # 结果帧＝该步收口：闭开着的同名 span；无 start 可闭（理论不可达）则合成立即闭，不悬空
        span = self._open.pop(node, None) or self._child(self._root, node, {"attempt": f["attempt"]})
        _stamp(span, {"attempt": f["attempt"], "status": f["status"], "ok": f["ok"],
                      "duration_ms": f.get("duration_ms"),
                      "tokens_in": f.get("tokens_in"), "tokens_out": f.get("tokens_out"),
                      "gen_ai.usage.input_tokens": f.get("tokens_in"),
                      "gen_ai.usage.output_tokens": f.get("tokens_out")})
        span.end()

    def close(self) -> None:
        """收口（幂等——run_question finally 与调用方双路都只生效一次）：
        未闭合节点 span（interrupt 暂停/裸异常掀翻）如实闭掉，root 落丢帧计数后结束。"""
        if self._closed:
            return
        self._closed = True
        for span in self._open.values():
            span.end()
        self._open.clear()
        if self._dropped:
            self._root.set_attribute("qadata.frames_dropped", self._dropped)
        self._root.end()
