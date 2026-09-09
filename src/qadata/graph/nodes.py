"""图节点：understand → explore → generate → execute → verify → respond（M2 自纠错）。"""
import re

from qadata.config import FALLBACK_SETTINGS, Settings
from qadata.graph.precise import NO_MAJORITY_ERROR, run_precise_batch
from qadata.graph.prompts import (
    format_failure_history,
    respond_prompt,
    sql_prompt,
    strip_conclusion_prefix,
    understand_prompt,
)
from qadata.graph.verify import verify_result
from qadata.llm.tracing import timed_invoke
from qadata.tools.db import open_readonly
from qadata.tools.executor import execute_sql
from qadata.tools.schema import build_schema_context
from qadata.types import Answer, QueryResult, SqlAttempt

_FENCE_RE = re.compile(r"```[a-zA-Z]*\n(.*?)```", re.DOTALL)

PREVIEW_ROWS = 10  # 喂给 respond prompt 的结果预览行数（表格与 n= 均由此派生）


def extract_sql(text: str) -> str:
    """剥掉 Markdown 围栏取 SQL；提取不到抛 ValueError。"""
    m = _FENCE_RE.search(text)
    raw = m.group(1) if m else text
    sql = raw.strip().strip(";").strip()
    if not sql or not sql.upper().startswith(("SELECT", "WITH")):
        raise ValueError(f"回复中未找到合法 SQL：{text[:80]!r}")
    # 多语句串（如 "SELECT 1; DROP x"）此处不拆分：最终防线在执行器的 sqlite3
    # 单语句 execute + mode=ro 只读连接双重闸（M1 分层设计，有意为之）。
    return sql


def _last_good_sql(attempts: list[SqlAttempt]) -> str | None:
    """最近一次执行成功的 SQL（答案稳定性回退候选；不新增状态键，从 attempts 派生）。"""
    for a in reversed(attempts):
        if a.sql and a.error is None:
            return a.sql
    return None


def _format_preview(columns: list[str], rows: list[tuple]) -> str:
    head = " | ".join(columns)
    body = "\n".join(" | ".join(str(v) for v in r) for r in rows)
    return f"{head}\n{body}" if body else head


def format_rows(result: QueryResult, limit: int = PREVIEW_ROWS) -> str:
    return _format_preview(result.columns, result.rows[:limit])


def make_nodes(llm, tracer=None, settings: Settings | None = None, limiter=None,
               skip_respond: bool = False):
    """节点工厂：闭包注入 llm/tracer/settings/limiter，便于测试时替换假模型与配置。
    skip_respond：评测模式——成功路径不生成结论文本（判分只读 answer.sql 的执行结果），
    失败诚实汇报与回退重执行不受影响。产品路径（ask/Web）默认 False，全家桶保留。"""
    s = settings or FALLBACK_SETTINGS

    def understand(state: dict) -> dict:
        q = timed_invoke(llm, understand_prompt(state["question"]), "understand", tracer, limiter)
        return {"original_question": state["question"], "question": str(q).strip()}

    def explore(state: dict) -> dict:
        conn = open_readonly(state["db_path"])
        try:
            ctx = build_schema_context(conn, state["question"], llm=llm, tracer=tracer,
                                       db_path=state["db_path"], limiter=limiter)
        finally:
            conn.close()
        return {"db_schema": ctx}

    def generate(state: dict) -> dict:
        history = format_failure_history(state.get("attempts", []), state.get("verify_note"))
        prompt = sql_prompt(
            schema=state.get("db_schema", ""),
            evidence=state.get("evidence", ""),
            question=state["question"],
            history=history,
        )
        if s.precise_candidates > 1:
            # 精准模式：同一 prompt 连打 K 发（temperature 由 build_llm 按候选数切换，
            # 多样性已探针验证）；提取失败的候选丢弃，全灭走提取失败入账路径
            sqls, extract_fails = [], 0
            for _ in range(s.precise_candidates):
                text = timed_invoke(llm, prompt, "generate", tracer, limiter)
                try:
                    sqls.append(extract_sql(str(text)))
                except ValueError:
                    extract_fails += 1
            if tracer is not None and (sqls or extract_fails):
                tracer.log("precise_generate", valid=len(sqls), extraction_failed=extract_fails)
            if not sqls:
                err = f"精准模式 {s.precise_candidates} 次采样均未提取出合法 SQL"
                attempts = list(state.get("attempts", []))
                attempts.append(SqlAttempt(sql="", error=err))
                return {"current_sql": None, "last_error": err, "attempts": attempts,
                        "precise_candidates": None}
            return {"current_sql": sqls[0], "last_error": None, "precise_candidates": sqls}
        text = timed_invoke(llm, prompt, "generate", tracer, limiter)
        try:
            sql = extract_sql(str(text))
        except ValueError as e:
            attempts = list(state.get("attempts", []))
            attempts.append(SqlAttempt(sql="", error=str(e)))
            return {"current_sql": None, "last_error": str(e), "attempts": attempts}
        return {"current_sql": sql, "last_error": None}

    def execute(state: dict) -> dict:
        candidates = state.get("precise_candidates")
        if candidates and len(candidates) > 1:
            # 票决轮（M4-C）：批量=一轮账本，attempts 只记一条；用完显式清载荷键
            outcome = run_precise_batch(state["db_path"], candidates,
                                        max_rows=s.max_rows, timeout_s=s.sql_timeout_s,
                                        tracer=tracer)
            attempts = list(state.get("attempts", []))
            if outcome.winner_sql is None:
                err = f"精准模式 {len(candidates)} 个候选全部执行失败"
                if outcome.fail_excerpt:
                    err += f"：{outcome.fail_excerpt}"
                attempts.append(SqlAttempt(sql="", error=err))
                return {"attempts": attempts, "result": None, "last_error": err,
                        "precise_candidates": None}
            if outcome.no_majority:
                attempts.append(SqlAttempt(sql="", error=NO_MAJORITY_ERROR))
            else:
                attempts.append(SqlAttempt(sql=outcome.winner_sql,
                                           row_count=outcome.winner_result.row_count))
            return {"attempts": attempts, "current_sql": outcome.winner_sql,
                    "result": outcome.winner_result, "last_error": None,
                    "precise_candidates": None}
        sql = state.get("current_sql")
        if not sql:
            # generate 阶段已失败：显式清掉上一轮残留的 result（整值覆盖语义下
            # "清除"必须显式返回），否则条件边①会拿陈旧 result 误走 verify 路径
            return {"result": None}
        attempts = list(state.get("attempts", []))
        try:
            res = execute_sql(
                state["db_path"], sql,
                max_rows=s.max_rows, timeout_s=s.sql_timeout_s,
            )
        except Exception as e:  # noqa: BLE001 自纠错账本：任何执行失败都要入账供重试
            msg = str(e)
            attempts.append(SqlAttempt(sql=sql, error=msg))
            return {"attempts": attempts, "result": None, "last_error": msg}
        attempts.append(SqlAttempt(sql=sql, row_count=res.row_count))
        return {"attempts": attempts, "result": res, "last_error": None}

    def verify(state: dict) -> dict:
        res = state.get("result")
        if res is None:
            return {"verify_note": None}  # 执行已失败：路由直接走兜底，无需校验
        last = state.get("attempts", [])[-1] if state.get("attempts") else None
        if last is not None and not last.sql and last.error == NO_MAJORITY_ERROR:
            # 票决不能自证：无多数派一律判可疑（不计正确路线）→ 有预算重试/耗尽带标注
            return {"verify_note": NO_MAJORITY_ERROR}
        verdict = verify_result(state["question"], state.get("current_sql") or "", res)
        return {"verify_note": None if verdict.passed else verdict.reason}

    def respond(state: dict) -> dict:
        res = state.get("result")
        sql = state.get("current_sql")
        attempts = state.get("attempts", [])
        fallback_note = None
        if res is None and attempts and attempts[-1].sql and attempts[-1].error:
            # 执行失败耗尽（非提取失败耗尽，后者必须诚实失败——M2 钉死回归）：
            # 回退重执行最近成功候选，答案不丢失；重执行失败则维持原诚实失败路径
            good_sql = _last_good_sql(attempts)
            if good_sql:
                try:
                    res = execute_sql(state["db_path"], good_sql,
                                      max_rows=s.max_rows, timeout_s=s.sql_timeout_s)
                    sql = good_sql
                    fallback_note = "最终尝试失败，以下为最近一次成功执行的查询结果"
                except Exception:  # noqa: BLE001, S110 回退是锦上添花：炸了不得连累原诚实失败路径
                    pass
        if res is None:
            # 永不编造：失败路径不调 LLM；汇报全部尝试（比 M1 单错误版信息量更高）
            if attempts:
                lines = [f"未能完成查询（共尝试 {len(attempts)} 次）："]
                for i, a in enumerate(attempts, 1):
                    err = (a.error or "未知错误").splitlines()[0]
                    lines.append(f"{i}. {a.sql or '（未提取到 SQL）'} → {err}")
                conclusion = "\n".join(lines)
            else:
                conclusion = f"未能完成查询：{state.get('last_error') or '未知错误'}"
            return {
                "answer": Answer(
                    conclusion=conclusion,
                    sql=sql,
                    result=None,
                    failed=True,
                    error_summary=state.get("last_error"),
                )
            }
        preview = res.rows[:PREVIEW_ROWS]  # 预览行只算一次，表格与 n= 同源派生
        if skip_respond:
            # 评测模式：结论不参与判分（_run_one 只重执行 answer.sql），占位文本省
            # 一次 LLM 调用；verify/回退标注仍按原逻辑挂上（确定性，不烧 token）
            conclusion = "（评测模式：跳过结论生成）"
        else:
            rows_table = _format_preview(res.columns, preview)
            if res.truncated:
                # 截断提示：让模型如实措辞、勿把截断行当全量（仅影响 prompt 呈现，不参与判分）
                rows_table += "\n（注意：结果已截断，实际行数可能更多）"
            text = timed_invoke(
                llm,
                respond_prompt(
                    question=state["question"],
                    sql=sql or "",
                    rows_table=rows_table,
                    total=res.row_count,
                    n=len(preview),
                ),
                "respond",
                tracer,
                limiter,
            )
            conclusion = strip_conclusion_prefix(str(text))
        note = state.get("verify_note")
        if note:  # 可疑但预算耗尽：数据真实，如实呈现＋标注（不是假失败）
            conclusion += f"\n（注意：该结果未通过自动校验：{note}）"
        if fallback_note:  # 回退作答：数据真实，如实标注来源
            conclusion += f"\n（注意：{fallback_note}）"
        return {"answer": Answer(conclusion=conclusion, sql=sql, result=res, failed=False)}

    return {
        "understand": understand,
        "explore": explore,
        "generate": generate,
        "execute": execute,
        "verify": verify,
        "respond": respond,
    }
