"""M7 票 01/02 web 薄壳：应用工厂＋DI（假模型/库注册表注入）＋前端同源服务。

唯一入口不破：/api/ask 只经 run_question（沙箱四层无旁路），本模块不建模型、
不读密钥、不碰 sqlite——llm/settings 由调用方注入（`qadata serve` 或测试的
ScriptedLLM）。票 02 双轨：A 预置注册表经 dbs 参数注入（装配归 serve）；
B 导入轨——上传落盘 web_imports／本地路径直连，两入口都进列表、可起别名。
导入只做文件搬运与登记，从不建连接（唯一入口钉测见 tests/test_web_api.py：
本包 AST 级禁 sqlite3，导入路径问答仍走 open_readonly＋URI 转义）。
契约形状由 tests/test_web_api.py 钉死（spec：/api/ask 12 字段改形状＝跨票改卷）。
"""
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from qadata.config import Settings
from qadata.graph.build import run_question
from qadata.types import Answer
from qadata.web.dbs import (
    DbEntry,
    DbRegistryError,
    sanitize_alias,
    store_upload,
    validate_import_path,
)

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


class LocalImportRequest(BaseModel):
    path: str = Field(min_length=1)
    alias: str = ""


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


def _entry_payload(entry: DbEntry) -> dict[str, str]:
    return {"name": entry.name, "evidence": entry.evidence, "source": entry.source}


def create_app(llm=None, settings: Settings | None = None,
               dbs: dict[str, str | DbEntry] | None = None,
               static_dir: str | Path | None = None,
               tracer=None,
               import_dir: str | Path | None = None) -> FastAPI:
    """应用工厂：llm/dbs/settings/static_dir/tracer/import_dir 全部由调用方注入——
    契约测试不碰网、不碰真实数据目录；库装配（YAML/目录发现/web_imports 重扫）
    是 serve 的责任，工厂不摸文件系统做发现。

    dbs 值可为裸路径 str（无默认口径的预置库）或 DbEntry。import_dir＝上传轨
    落盘目录；None 时上传端点诚实拒绝（直连轨不受影响）。
    llm=None 时逐题经 run_question 内部 build_llm（需密钥）；生产路径由 serve 注入
    共享实例（与 eval 多线程共享单实例同构）。settings/tracer 一并透传 run_question。
    static_dir 默认 web/dist 沿仓库根 cwd 约定（与 metrics_dir、TRACE_PATH 同款）。
    """
    # 单进程单用户 demo（spec 会话节同款约定）：闭包字典被端点读写，
    # _register 查后设的竞态在本卷接受，公网部署触发器另案。
    registry: dict[str, DbEntry] = {
        name: (v if isinstance(v, DbEntry) else DbEntry(name=name, path=v))
        for name, v in (dbs or {}).items()
    }
    uploads_to = Path(import_dir) if import_dir is not None else None
    dist = Path(static_dir) if static_dir is not None else Path("web/dist")
    app = FastAPI(title="qadata-web", docs_url=None, redoc_url=None)

    def _register(entry: DbEntry) -> None:
        if entry.name in registry:
            raise HTTPException(status_code=409, detail=f"别名已存在：{entry.name}")
        registry[entry.name] = entry

    @app.get("/api/dbs")
    def list_dbs() -> dict[str, list[dict[str, str]]]:
        # 票 02 起条目含默认口径与来源轨（前端预填/徽标）；/api/ask 12 字段不动
        return {"dbs": [_entry_payload(registry[n]) for n in sorted(registry)]}

    @app.post("/api/dbs/upload")
    async def upload_db(request: Request,
                        name: str = Query(..., description="原始文件名（仅 basename）"),
                        alias: str = Query("", description="列表展示别名，缺省取文件名主干")) -> dict[str, str]:
        if uploads_to is None:
            raise HTTPException(status_code=400, detail="本服务未开启上传（import_dir 未配置）")
        try:
            final_alias = sanitize_alias(alias or Path(name).stem)
            # 落盘名以别名为准——重启后 discover_imports 按文件名主干恢复，别名不丢
            target = uploads_to / f"{final_alias}{Path(name).suffix.lower()}"
            if target.exists():
                raise HTTPException(status_code=409, detail=f"同名导入库已存在：{target.name}")
            dest = store_upload(uploads_to, await request.body(), target.name)
        except DbRegistryError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None
        entry = DbEntry(name=final_alias, path=str(dest), source="import")
        _register(entry)
        return _entry_payload(entry)

    @app.post("/api/dbs/local")
    def import_local(req: LocalImportRequest) -> dict[str, str]:
        # 只登记路径，不建连接：能不能打开交给问一题时的沙箱唯一入口如实回答
        try:
            resolved = validate_import_path(req.path)
            final_alias = sanitize_alias(req.alias or Path(resolved).stem)
        except DbRegistryError as e:
            raise HTTPException(status_code=400, detail=str(e)) from None
        entry = DbEntry(name=final_alias, path=resolved, source="import")
        _register(entry)
        return _entry_payload(entry)

    @app.post("/api/ask")
    def ask(req: AskRequest) -> dict[str, Any]:
        entry = registry.get(req.db)
        if entry is None:
            # 注册表外拒在调模型之前：web 面不提供更名数据库的旁路
            raise HTTPException(status_code=404, detail=f"未知数据库：{req.db}")
        # 口径单一来源：请求未显式给口径时用库默认（预置 YAML/指标派生），空则如实空
        evidence = req.evidence.strip() or entry.evidence
        answer = run_question(entry.path, req.question, evidence=evidence,
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
