"""M7 票 01 起服入口：`qadata serve` 的全部逻辑在此（CLI 保持薄壳）。

生产路径在此收口：load_settings→build_llm 一次建共享实例（与 eval 多线程共享
单实例同构）→create_app 注入。测试不走到这——契约测试直接 create_app 注假模型。
"""
import uvicorn

from qadata.config import load_settings
from qadata.llm.gateway import build_llm
from qadata.llm.tracing import TRACE_PATH, TraceLogger
from qadata.web.app import create_app
from qadata.web.dbs import DEFAULT_DB_DIR, discover_dbs


def run_server(host: str = "127.0.0.1", port: int = 8000,
               db_dir: str | None = None) -> None:
    settings = load_settings()  # 缺密钥在这里诚实报错（.env 参照 .env.example）
    dbs = discover_dbs(db_dir or DEFAULT_DB_DIR)
    app = create_app(llm=build_llm(settings), settings=settings, dbs=dbs,
                     tracer=TraceLogger(TRACE_PATH))
    print(f"问数 web demo：http://{host}:{port}"
          f"（预置库 {len(dbs)} 个：{', '.join(sorted(dbs)) or '无——检查 --db-dir'}）")
    uvicorn.run(app, host=host, port=port)
