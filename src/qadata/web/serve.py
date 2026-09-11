"""M7-rev2 票 02.5 起服入口：`qadata serve` 的全部逻辑在此（CLI 保持薄壳）。

生产路径在此收口：load_settings→AgentStore（data/agents 真空启动，无任何预置）
→build_llm 一次建共享实例（与 eval 多线程共享单实例同构）→create_app 注入。
测试不走到这——契约测试直接 create_app 注假模型＋tmp 存储。
"""
import uvicorn

from qadata.config import load_settings
from qadata.llm.gateway import build_llm
from qadata.llm.tracing import TRACE_PATH, TraceLogger
from qadata.web.agents import DEFAULT_AGENTS_DIR, AgentStore
from qadata.web.app import create_app


def run_server(host: str = "127.0.0.1", port: int = 8000,
               agents_dir: str = DEFAULT_AGENTS_DIR) -> None:
    settings = load_settings()  # 缺密钥在这里诚实报错（.env 参照 .env.example）
    store = AgentStore(agents_dir, metrics_dir=settings.metrics_dir)
    app = create_app(llm=build_llm(settings), settings=settings, agents=store,
                     tracer=TraceLogger(TRACE_PATH))
    print(f"问数 web demo：http://{host}:{port}"
          f"（当前模型 {settings.model}·真源 .env·只读展示；智能体目录 {agents_dir}）")
    uvicorn.run(app, host=host, port=port)
