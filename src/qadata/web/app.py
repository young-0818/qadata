"""M7 票 01 web 薄壳：应用工厂＋DI（假模型/库注册表注入）＋前端同源服务。

唯一入口不破：/api/ask 只经 run_question（沙箱四层无旁路），本模块不建模型、
不读密钥、不碰 sqlite——llm/settings 由调用方注入（`qadata serve` 或测试的
ScriptedLLM）。契约形状由 tests/test_web_api.py 钉死（spec：改形状＝跨票改卷）。
"""
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from qadata.config import Settings
from qadata.graph.build import run_question
from qadata.types import Answer

# 前端未构建时的诚实占位页（侦察笔记：别白屏，写明构建命令）
_PLACEHOLDER_PAGE = """<!doctype html>
<html lang="zh-CN">
<meta charset="utf-8">
<title>问数 · 前端未构建</title>
<body style="font-family: system-ui; max-width: 40rem; margin: 4rem auto">
<h1>前端尚未构建</h1>
<p>API 已在服务（<code>GET /api/dbs</code> / <code>POST /api/ask</code>）。要看到对话页面，请在仓库根目录执行：</p>
<pre style="background:#f4f4f5; padding:.75rem; border-radius:.6rem"><code>cd web
npm install
npm run build</code></pre>
<p>构建完成后刷新本页。</p>
</body>
</html>
"""


class AskRequest(BaseModel):
    db: str
    question: str = Field(min_length=1)
    evidence: str = ""


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


def create_app(llm=None, settings: Settings | None = None,
               dbs: dict[str, str] | None = None,
               static_dir: str | Path | None = None,
               tracer=None) -> FastAPI:
    """应用工厂：llm/dbs/settings/static_dir/tracer 全部由调用方注入——契约测试
    不碰网、不碰真实数据目录；库发现（discover_dbs）是 serve 的装配责任，工厂不摸文件系统。

    llm=None 时逐题经 run_question 内部 build_llm（需密钥）；生产路径由 serve 注入
    共享实例（与 eval 多线程共享单实例同构）。settings/tracer 一并透传 run_question。
    static_dir 默认 web/dist 沿仓库根 cwd 约定（与 metrics_dir、TRACE_PATH 同款）。
    """
    registry = dict(dbs) if dbs is not None else {}
    dist = Path(static_dir) if static_dir is not None else Path("web/dist")
    app = FastAPI(title="qadata-web", docs_url=None, redoc_url=None)

    @app.get("/api/dbs")
    def list_dbs() -> dict[str, list[str]]:
        return {"dbs": sorted(registry)}

    @app.post("/api/ask")
    def ask(req: AskRequest) -> dict[str, Any]:
        db_path = registry.get(req.db)
        if db_path is None:
            # 注册表外拒在调模型之前：web 面不提供更名数据库的旁路
            raise HTTPException(status_code=404, detail=f"未知数据库：{req.db}")
        answer = run_question(db_path, req.question, evidence=req.evidence,
                              llm=llm, settings=settings, tracer=tracer)
        return answer_to_payload(answer)

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
