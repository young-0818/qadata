"""SQL 执行器（M1 临时版：只读连接 + 行数上限）。

M2 将升级为完整四层沙箱（sqlglot 白名单/超时/资源限制），见设计文档 §6。
"""
import sqlite3
import time

from qadata.types import QueryResult, SqlExecutionError


def execute_sql(db_path: str, sql: str, max_rows: int = 50) -> QueryResult:
    stripped = sql.strip().rstrip(";").strip()
    head = stripped.split(None, 1)[0].upper() if stripped else ""
    if head not in ("SELECT", "WITH"):
        raise SqlExecutionError(f"只允许只读查询（SELECT/WITH），收到：{head or '空语句'}")

    t0 = time.perf_counter()
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)  # 物理只读
    try:
        cur = conn.execute(stripped)
        columns = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(max_rows + 1)
        truncated = len(rows) > max_rows
        if truncated:
            rows = rows[:max_rows]
        elapsed = int((time.perf_counter() - t0) * 1000)
        return QueryResult(
            columns=columns, rows=[tuple(r) for r in rows],
            row_count=len(rows), truncated=truncated, elapsed_ms=elapsed,
        )
    except sqlite3.Error as e:
        raise SqlExecutionError(str(e)) from e
    finally:
        conn.close()
