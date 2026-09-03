"""连接层（沙箱第①层）：只读连接统一入口。

三处手拼 file:...?mode=ro URI（nodes/executor/bird）收拢于此，
as_uri 负责盘符与特殊字符转义（Task 4 缓期项一并解决）。
"""
import sqlite3
from pathlib import Path
from urllib.parse import quote

from qadata.types import SqlExecutionError


def as_uri(db_path: str) -> str:
    """本地路径 → sqlite file URI（只读）。Windows 盘符、空格、中文等一律安全转义。"""
    posix = Path(db_path).as_posix()
    if len(posix) >= 2 and posix[1] == ":":  # Windows 盘符：C:/... → file:///C:/...
        encoded = "///" + posix[:2] + quote(posix[2:], safe="/")
    else:  # POSIX 绝对/相对路径
        encoded = quote(posix, safe="/")
    return f"file:{encoded}?mode=ro"


def open_readonly(db_path: str) -> sqlite3.Connection:
    """物理只读打开；路径无效收敛为可读的 SqlExecutionError。"""
    try:
        return sqlite3.connect(as_uri(db_path), uri=True)
    except sqlite3.Error as e:
        raise SqlExecutionError(f"无法打开数据库：{e}") from e
