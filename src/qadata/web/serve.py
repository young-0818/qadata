"""M7 票 01/02 起服入口：`qadata serve` 的全部逻辑在此（CLI 保持薄壳）。

生产路径在此收口：load_settings→库装配（A 轨 YAML 预置注册表接管目录发现，
缺文件回退 BIRD 风格扫描并如实打印；B 轨重启重扫 web_imports）→build_llm 一次
建共享实例（与 eval 多线程共享单实例同构）→create_app 注入。测试不走到这——
契约测试直接 create_app 注假模型。
"""
from pathlib import Path

import uvicorn

from qadata.config import load_settings
from qadata.llm.gateway import build_llm
from qadata.llm.tracing import TRACE_PATH, TraceLogger
from qadata.web.app import create_app
from qadata.web.dbs import (
    DEFAULT_DB_DIR,
    DEFAULT_IMPORT_DIR,
    DEFAULT_REGISTRY_PATH,
    DbEntry,
    discover_dbs,
    discover_imports,
    load_preset_registry,
)


def assemble_dbs(registry_path: str | Path = DEFAULT_REGISTRY_PATH,
                 db_dir: str | Path | None = None,
                 import_dir: str | Path = DEFAULT_IMPORT_DIR,
                 *, metrics_dir: str = "metrics") -> dict[str, DbEntry]:
    """双轨库装配（纯装配，不建连接）：A 轨 YAML 优先、缺文件回退目录发现；
    再并上 web_imports 重扫（重启恢复历史上传）。撞名预置轨优先占名，
    导入侧如实跳过——宁缺勿错，不静默改名。"""
    reg = Path(registry_path)
    if reg.is_file():
        dbs = load_preset_registry(reg, metrics_dir=metrics_dir)
        note = f"A 轨：YAML 预置注册表 {reg}（{len(dbs)} 库）"
    else:
        found = discover_dbs(db_dir or DEFAULT_DB_DIR)
        dbs = {k: DbEntry(name=k, path=v) for k, v in found.items()}
        note = f"A 轨：缺 {reg}，回退目录发现 {db_dir or DEFAULT_DB_DIR}（{len(dbs)} 库）"
    hidden: list[str] = []
    for name, entry in discover_imports(import_dir).items():
        if name in dbs:
            hidden.append(name)
        else:
            dbs[name] = entry
    if hidden:
        note += f"；B 轨 web_imports 撞名跳过：{'、'.join(hidden)}"
    print(note)
    return dbs


def run_server(host: str = "127.0.0.1", port: int = 8000,
               db_dir: str | None = None,
               registry: str = DEFAULT_REGISTRY_PATH,
               import_dir: str = DEFAULT_IMPORT_DIR) -> None:
    settings = load_settings()  # 缺密钥在这里诚实报错（.env 参照 .env.example）
    dbs = assemble_dbs(registry, db_dir, import_dir, metrics_dir=settings.metrics_dir)
    app = create_app(llm=build_llm(settings), settings=settings, dbs=dbs,
                     tracer=TraceLogger(TRACE_PATH), import_dir=import_dir)
    print(f"问数 web demo：http://{host}:{port}"
          f"（在册库 {len(dbs)} 个：{', '.join(sorted(dbs)) or '无'}；"
          f"上传落盘 {import_dir}）")
    uvicorn.run(app, host=host, port=port)
