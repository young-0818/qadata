"""预置库自动发现：BIRD 风格目录树 → 库名注册表（票 01 最小版，双轨制在票 02 展开）。

约定形状 <root>/<name>/<name>.sqlite；不符合者诚实跳过（宁缺勿错，与指标层同气）。
"""
from pathlib import Path

# 演示库根目录（仓库默认，qadata serve --db-dir 可覆盖）
DEFAULT_DB_DIR = "data/bird/dev/dev_databases"


def discover_dbs(root: str | Path) -> dict[str, str]:
    """扫描 root 下 <name>/<name>.sqlite，返回 {库名: 路径}；目录缺失返回空 dict。"""
    out: dict[str, str] = {}
    base = Path(root)
    if not base.is_dir():
        return out
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        candidate = child / f"{child.name}.sqlite"
        if candidate.is_file():
            out[child.name] = str(candidate)
    return out
