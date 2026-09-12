"""M7-rev2 票 02.5/03 web 薄壳：应用工厂＋DI＋智能体面＋进度流＋前端同源服务。

唯一入口不破：/api/ask 与 /api/ask/stream 只经 run_question（沙箱四层无旁路），
本模块不建模型、不读密钥、不碰 sqlite——llm/settings/agents（AgentStore）由
调用方注入（serve 或测试的 ScriptedLLM＋tmp 存储）。智能体＝一只目录
（data/agents/<id>/），数据源上传唯一路、业务知识引用态读取期派生；schema 摘要
经 open_readonly 只读打开（导入落盘零建连、读取展示走沙箱①层——三层钉测见契约
测试）。/api/ask 响应 12 字段冻结不动（spec 响应契约：改形状＝跨票改卷；
session_id 恒 null 至票 04）。票 03：/api/ask/stream 以 SSE 直播节点级进度帧
（node/attempt/status 三字段起步）＋末帧 event:answer（即 12 字段契约本体）；
agent 级在途锁两端点共用（同问在途＝409）。
"""
import json
import queue
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from qadata.config import Settings
from qadata.graph.build import run_question
from qadata.tools.db import open_readonly
from qadata.tools.schema import list_tables
from qadata.types import Answer
from qadata.web.agents import (
    AgentMeta,
    AgentNotFound,
    AgentStore,
    AgentStoreError,
)

# 前端未构建时的诚实占位页（侦察笔记：别白屏，写明构建命令）
_PLACEHOLDER_PAGE = """<!doctype html>
<html lang="zh-CN">
<meta charset="utf-8">
<title>问数 · 前端未构建</title>
<body style="font-family: system-ui; max-width: 40rem; margin: 4rem auto">
<h1>前端尚未构建</h1>
<p>API 已在服务（<code>GET /api/agents</code> / <code>POST /api/ask</code>）。要看到页面，请在仓库根目录执行：</p>
<pre style="background:#f4f4f5; padding:.75rem; border-radius:.6rem"><code>cd web
npm install
npm run build</code></pre>
<p>构建完成后刷新本页。</p>
</body>
</html>
"""


class AskRequest(BaseModel):
    agent_id: str
    question: str = Field(min_length=1)
    evidence: str = ""


class AgentCreateRequest(BaseModel):
    name: str
    description: str = ""


class AgentPatchRequest(BaseModel):
    # 全部可选：PATCH 只动显式传入的字段（model_fields_set 即"传了哪些"）
    name: str | None = None
    description: str | None = None
    evidence: str | None = None
    metrics_ref: str | None = None
    preset_questions: list[str] | None = None


def answer_to_payload(answer: Answer) -> dict[str, Any]:
    """Answer/QueryResult → 契约 JSON（票 01 冻结 12 字段；无结果集时行列如实 null）。

    session_id 恒 null——多轮在票 04，届时才有真值。
    """
    res = answer.result
    return {
        "conclusion": answer.conclusion,
        "sql": answer.sql,
        "columns": list(res.columns) if res else None,
        "rows": [list(row) for row in res.rows] if res else None,
        "truncated": res.truncated if res else None,
        "elapsed_ms": res.elapsed_ms if res else None,
        "failed": answer.failed,
        "error_summary": answer.error_summary,
        "path": answer.path,
        "metric_name": answer.metric_name,
        "template_fell_back": answer.template_fell_back,
        "session_id": None,
    }


def create_app(agents: AgentStore, llm=None, settings: Settings | None = None,
               static_dir: str | Path | None = None,
               tracer=None) -> FastAPI:
    """应用工厂：agents（AgentStore）必填、llm/settings/static_dir/tracer 注入——
    契约测试注 tmp 存储＋假模型，工厂不摸文件系统做装配（评审收紧：删 None 兜底，
    不给"自己造真目录 store"留后路）。

    llm=None 时逐题经 run_question 内部 build_llm（需密钥）；生产路径由 serve 注入
    共享实例。static_dir 默认 web/dist 沿仓库根 cwd 约定（与 TRACE_PATH 同款）。
    """
    store = agents
    dist = Path(static_dir) if static_dir is not None else Path("web/dist")
    app = FastAPI(title="qadata-web", docs_url=None, redoc_url=None)

    def _bad(e: AgentStoreError) -> HTTPException:
        if isinstance(e, AgentNotFound):
            return HTTPException(status_code=404, detail=str(e))
        return HTTPException(status_code=400, detail=str(e))

    def _datasource_info(meta: AgentMeta) -> dict[str, Any]:
        path = store.datasource_path(meta)
        if path is None:
            return {"has_file": False, "table_count": None, "error": None}
        try:
            conn = open_readonly(str(path))  # 沙箱①层：摘要展示也走唯一只读入口
            try:
                return {"has_file": True, "table_count": len(list_tables(conn)),
                        "error": None}
            finally:
                conn.close()
        except Exception as e:  # noqa: BLE001 摘要展示层：打不开一律如实上报不装正常
            # （含 sqlite3 系异常——web 包 import 纪律不引 sqlite3，捕获面在此宽为有意为之）
            return {"has_file": True, "table_count": None, "error": str(e)}

    def _detail(meta: AgentMeta) -> dict[str, Any]:
        try:
            kb, kb_err = store.effective_evidence(meta), None
        except AgentStoreError as e:
            kb, kb_err = "", str(e)
        return {"id": meta.id, "name": meta.name, "description": meta.description,
                "evidence": meta.evidence, "metrics_ref": meta.metrics_ref,
                "business_knowledge": kb, "business_knowledge_error": kb_err,
                "preset_questions": list(meta.preset_questions),
                "datasource": _datasource_info(meta)}

    # ── 模型配置：只读卡（可写化＝后手裁决；.env 是模型唯一真源）──────

    @app.get("/api/model")
    def model_card() -> dict[str, Any]:
        return {"model": settings.model if settings else None,
                "source": "环境 .env → QADATA_MODEL", "writable": False}

    @app.get("/api/metrics-registries")
    def metric_registries() -> dict[str, list[str]]:
        return {"registries": store.registries()}

    # ── 智能体 CRUD（真空启动：初始为空列表）────────────────────────

    @app.get("/api/agents")
    def list_agents() -> dict[str, list[dict[str, Any]]]:
        try:
            metas = store.all()
        except AgentStoreError as e:
            raise _bad(e) from None
        return {"agents": [{"id": m.id, "name": m.name, "description": m.description,
                            "has_datasource": store.datasource_path(m) is not None}
                           for m in metas]}

    @app.post("/api/agents")
    def create_agent(req: AgentCreateRequest) -> dict[str, Any]:
        try:
            meta = store.create(req.name, req.description)
        except AgentStoreError as e:
            raise _bad(e) from None
        return _detail(meta)

    @app.get("/api/agents/{agent_id}")
    def get_agent(agent_id: str) -> dict[str, Any]:
        try:
            return _detail(store.get(agent_id))
        except AgentStoreError as e:
            raise _bad(e) from None

    @app.patch("/api/agents/{agent_id}")
    def patch_agent(agent_id: str, req: AgentPatchRequest) -> dict[str, Any]:
        try:
            return _detail(store.patch(agent_id, **req.model_dump(exclude_unset=True)))
        except AgentStoreError as e:
            raise _bad(e) from None

    @app.delete("/api/agents/{agent_id}")
    def delete_agent(agent_id: str) -> dict[str, bool]:
        try:
            store.delete(agent_id)
        except AgentStoreError as e:
            raise _bad(e) from None
        return {"ok": True}

    @app.post("/api/agents/{agent_id}/datasource")
    async def upload_datasource(agent_id: str, request: Request,
                                name: str = Query(..., description="原始文件名（仅用于扩展名白名单）"),
                                ) -> dict[str, bool]:
        # 只搬字节进智能体目录：本端点全程零建连（连 _detail 都不走），
        # 库能不能打开由详情摘要/问答链路经 open_readonly 如实回答
        try:
            store.store_datasource(agent_id, await request.body(), name)
        except AgentStoreError as e:
            raise _bad(e) from None
        return {"ok": True}

    # ── 问数：唯一入口，智能体定位数据源与默认业务知识 ───────────────
    # 在途锁（票 03）：agent 级、/api/ask 与 /api/ask/stream 共用一把——防阻塞×流式
    # 交错。单进程单用户约定（与上传闭包竞态同款，M7 spec）；票 04 会话制落地后
    # 键升为 session_id（票面"同会话并发"），本票先按智能体粒度拒。
    _inflight: set[str] = set()
    _inflight_guard = threading.Lock()

    def _acquire_or_409(agent_id: str, name: str) -> None:
        with _inflight_guard:
            if agent_id in _inflight:
                raise HTTPException(
                    status_code=409,
                    detail=f"智能体「{name}」正在回答上一个问题，等进度流收口后再问")
            _inflight.add(agent_id)

    def _release(agent_id: str) -> None:
        with _inflight_guard:
            _inflight.discard(agent_id)

    def _resolve_ask_target(req: AskRequest) -> tuple[str, str, str]:
        """定位数据源＋口径优先级（请求显式 > 智能体业务知识 > 空）。
        阻塞与流式两端点共用＝拒绝文案与顺序严格一致（流式端点不得自创一套）。"""
        try:
            meta = store.get(req.agent_id)
            path = store.datasource_path(meta)
            if path is None:
                raise HTTPException(status_code=400,
                                    detail=f"智能体「{meta.name}」未配置数据源（详情页上传 .sqlite 后再问）")
            evidence = req.evidence.strip() or store.effective_evidence(meta)
        except AgentStoreError as e:
            raise _bad(e) from None
        return str(path), evidence, meta.name

    @app.post("/api/ask")
    def ask(req: AskRequest) -> dict[str, Any]:
        path, evidence, agent_name = _resolve_ask_target(req)
        _acquire_or_409(req.agent_id, agent_name)
        try:
            answer = run_question(path, req.question, evidence=evidence,
                                  llm=llm, settings=settings, tracer=tracer)
        finally:
            _release(req.agent_id)
        return answer_to_payload(answer)

    # ── 票 03：进度流直播（SSE）——治"黑盒感"的主治通道 ──────────────

    @app.post("/api/ask/stream")
    def ask_stream(req: AskRequest) -> StreamingResponse:
        """前置拒绝与 /api/ask 同序同文案（400/404/409 走普通 JSON 错误，不起流）；
        起流后：进度帧＝data: {node,attempt,status}\n\n，末帧＝event: answer＋
        12 字段契约本体（形状与 /api/ask 同源，不另造）。run_question 仍是最外层
        守护——任何失败都以诚实失败答案收口成末帧，流永不裸断。
        桥接：worker 线程跑阻塞图（on_event＝入队），同步生成器逐帧取队 yield
        （starlette 自动 threadpool 迭代）。在途＝run_question 计算在途：放锁挂在
        runner 的 finally（评审收紧·双轴同指），断流/生成器未启动等一切投递路径
        都不构成泄漏窗口；计算跑完前队列缓冲、跑完即弃（单进程单用户尾差）。"""
        path, evidence, agent_name = _resolve_ask_target(req)
        _acquire_or_409(req.agent_id, agent_name)
        events: queue.Queue = queue.Queue()
        box: dict[str, Answer] = {}
        sentinel = object()

        def _runner() -> None:
            try:
                box["answer"] = run_question(path, req.question, evidence=evidence,
                                             llm=llm, settings=settings, tracer=tracer,
                                             on_event=events.put)
            finally:
                events.put(sentinel)
                _release(req.agent_id)  # 在途＝计算在途：投递侧任何路径不持锁

        threading.Thread(target=_runner, daemon=True).start()

        def _frames():
            while True:
                item = events.get()
                if item is sentinel:
                    break
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
            answer = box.get("answer") or Answer(  # worker 被 BaseException 掀翻也不裸断
                conclusion="流式查询异常中断", failed=True, error_summary="流式查询异常中断")
            payload = json.dumps(answer_to_payload(answer), ensure_ascii=False)
            yield f"event: answer\ndata: {payload}\n\n"

        return StreamingResponse(_frames(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    if (dist / "index.html").is_file():
        # 构建产物同源服务（spec：零 CORS）；挂载放在 API 路由之后，不吃 /api/*
        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(dist / "index.html")

        if (dist / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
    else:

        @app.get("/", response_class=HTMLResponse)
        def placeholder() -> str:
            return _PLACEHOLDER_PAGE

    return app
