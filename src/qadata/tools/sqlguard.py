"""沙箱第②层：语句层静态校验（sqlglot，SQLite 方言）。

四步管线：解析 → 单语句 → 根节点白名单 → 引用表校验。
任一不过即拒；拒绝理由可读，作为失败历史喂给自纠错（设计文档 §8：沙箱拒绝原因明确告知模型）。
"""
import sqlglot
from sqlglot import exp

from qadata.types import SqlExecutionError

# 放行的根节点类型。实测（sqlglot 30.x）：WITH 查询根是 Select；
# UNION/INTERSECT/EXCEPT 各有类型；VACUUM 等归 Command——不在名单内即拒。
_ALLOWED_ROOTS = (exp.Select, exp.Union, exp.Intersect, exp.Except)


def validate_sql(sql: str, allowed_tables: list[str]) -> str:
    """静态校验；通过返回去首尾空白的原 SQL，不通过抛 SqlExecutionError。"""
    text = sql.strip()
    if not text:
        raise SqlExecutionError("SQL 安全检查未通过：空语句")

    # 1. 解析（显式 RAISE：语法错不烧执行机会）
    try:
        stmts = sqlglot.parse(text, dialect="sqlite", error_level=sqlglot.ErrorLevel.RAISE)
    except sqlglot.errors.ParseError as e:
        raise SqlExecutionError(
            f"SQL 安全检查未通过：语法解析失败——{str(e).splitlines()[0]}"
        ) from e

    # 2. 单语句（堵分号拼接走私）
    if len(stmts) != 1:
        raise SqlExecutionError(f"SQL 安全检查未通过：只允许单条语句，收到 {len(stmts)} 条")
    ast = stmts[0]

    # 3. 根节点白名单（CTE 包裹的变异语句根是 Insert 等，在此被拦）
    if ast is None or not isinstance(ast, _ALLOWED_ROOTS):
        kind = type(ast).__name__ if ast is not None else "空"
        raise SqlExecutionError(f"SQL 安全检查未通过：只允许 SELECT/WITH 查询，收到 {kind}")

    # 4. 引用表校验（排除 CTE 别名；大小写不敏感，报错保留原写法）
    allowed = {t.lower() for t in allowed_tables}
    unknown = sorted((t for t in _referenced_tables(ast) if t.lower() not in allowed),
                     key=str.lower)
    if unknown:
        raise SqlExecutionError(f"SQL 安全检查未通过：引用了不存在的表 {', '.join(unknown)}")
    return text


def _referenced_tables(ast) -> set[str]:
    """语句引用的物理表名（排除 CTE 别名、保留原写法）——表校验与血缘展示共用一份判据。"""
    cte_names = {c.alias.lower() for c in ast.find_all(exp.CTE)}
    return {t.name for t in ast.find_all(exp.Table)
            if t.name and t.name.lower() not in cte_names}


def used_tables(sql: str) -> list[str]:
    """SQL 所用表集合（票 07 respond 数据依据节：确定性、零 token）。

    显示辅助而非沙箱闸：解析失败/非单语句返回空表、绝不抛——拒绝语义归
    validate_sql，此处只诚实回答「我认不出来」（调用方省略该字段即可）。
    """
    try:
        stmts = sqlglot.parse(sql, dialect="sqlite", error_level=sqlglot.ErrorLevel.RAISE)
    except sqlglot.errors.ParseError:
        return []
    if len(stmts) != 1 or stmts[0] is None:
        return []
    return sorted(_referenced_tables(stmts[0]), key=str.lower)
