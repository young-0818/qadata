"""SQL 执行器（M2：沙箱②语句层前置＋③资源层）。

执行链路：只读连接 → sqlglot 语句校验 → 超时守护 → 取数（双行数上限）。
四层防御见设计文档 §6；①连接层在 tools/db.py。
"""
import sqlite3
import time

from qadata.tools.db import open_readonly
from qadata.tools.schema import list_tables
from qadata.tools.sqlguard import validate_sql
from qadata.types import QueryResult, SqlExecutionError

FETCH_CAP = 1000  # 获取上限（资源层）：row_count 真值探索的硬顶


def execute_sql(db_path: str, sql: str, *, max_rows: int = 50,
                fetch_cap: int = FETCH_CAP, timeout_s: float = 5.0) -> QueryResult:
    conn = open_readonly(db_path)  # ①连接层：物理只读＋路径错误收敛
    try:
        validate_sql(sql, list_tables(conn))  # ②语句层：任何违规不烧执行机会
        t0 = time.perf_counter()

        def _over_budget():  # ③资源层：超时中断（progress handler 每 1000 条指令查一次）
            return 1 if time.perf_counter() - t0 > timeout_s else 0

        conn.set_progress_handler(_over_budget, 1000)
        try:
            cur = conn.execute(sql.strip())
            columns = [d[0] for d in cur.description] if cur.description else []
            fetched = cur.fetchmany(fetch_cap + 1)
        except sqlite3.OperationalError as e:
            if "interrupt" in str(e).lower():
                raise SqlExecutionError(f"查询超时（>{timeout_s} 秒），已被沙箱中断") from e
            raise

        # row_count 真值：撞顶才花一条 COUNT(*) 小查询（设计 §3.2）。
        # 探索失败（多为与取数共享超时预算被中断）则降级为近似总数——
        # 取数已成功的合法查询不得被"锦上添花"的精确总数打成失败尝试
        if len(fetched) > fetch_cap:
            try:
                total = conn.execute(
                    f"SELECT COUNT(*) FROM ({sql.strip().rstrip(';')})"
                ).fetchone()[0]
            except sqlite3.Error:
                total = len(fetched)
        else:
            total = len(fetched)
        rows = [tuple(r) for r in fetched[:max_rows]]
        elapsed = int((time.perf_counter() - t0) * 1000)
        return QueryResult(
            columns=columns, rows=rows, row_count=int(total),
            truncated=total > len(rows), elapsed_ms=elapsed,
        )
    except sqlite3.Error as e:
        raise SqlExecutionError(str(e)) from e
    finally:
        conn.close()
