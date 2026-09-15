"""图节点：understand →〔metric_match〕→ explore → generate → execute → verify → respond。

M5 票 05：metric_match 为可选插入节点（总开关 Settings.metric_layer，默认关＝现状在
路由/调用数/账本/评测字段上逐行为一致；票 07 三节展示属两态共用的展示层）。"""
import re
from datetime import date, datetime
from pathlib import Path

from qadata.config import FALLBACK_SETTINGS, Settings
from qadata.graph.error_hints import error_hint
from qadata.graph.intent import parse_understand_response
from qadata.graph.metrics import (
    RegistryError,
    fill_slots,
    has_extreme_signal,
    lineage_text,
    load_registry,
    match_metric,
    parse_metric_review,
    render_sql,
)
from qadata.graph.precise import NO_MAJORITY_ERROR, run_precise_batch
from qadata.graph.prompts import (
    compose_conclusion,
    format_failure_history,
    format_session_draft,
    format_session_history,
    metric_review_prompt,
    respond_prompt,
    sql_prompt,
    strip_conclusion_prefix,
    understand_prompt,
)
from qadata.graph.verify import verify_result
from qadata.llm.tracing import BEIJING, timed_invoke
from qadata.tools.db import open_readonly
from qadata.tools.executor import execute_sql
from qadata.tools.schema import build_schema_context
from qadata.tools.sqlguard import used_tables
from qadata.types import Answer, QueryResult, SqlAttempt

_FENCE_RE = re.compile(r"```[a-zA-Z]*\n(.*?)```", re.DOTALL)

PREVIEW_ROWS = 10  # 喂给 respond prompt 的结果预览行数（表格与 n= 均由此派生）


def _today() -> date:
    """填槽时钟缝：纯函数 fill_slots 不读钟，由节点注入；测试 monkeypatch 本函数。
    取北京日历日（与 tracing 时间戳同口径），「去年」等相对词按中国时区解析。"""
    return datetime.now(BEIJING).date()


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


def _exec_fail_status(msg: str) -> str:
    """execute 失败帧文案（票 03）：错误首行＋中文修复建议（有才带，宁缺毋滥）。
    与建议同源＝error_hints（generate 重试历史吃的就是它，页面所见即模型下轮所读）。"""
    status = f"执行失败：{msg.splitlines()[0]}"
    hint = error_hint(msg)
    return f"{status}；修复建议：{hint}" if hint else status


def _format_preview(columns: list[str], rows: list[tuple]) -> str:
    head = " | ".join(columns)
    body = "\n".join(" | ".join(str(v) for v in r) for r in rows)
    return f"{head}\n{body}" if body else head


def format_rows(result: QueryResult, limit: int = PREVIEW_ROWS) -> str:
    return _format_preview(result.columns, result.rows[:limit])


def _basis_line(res: QueryResult, sql: str | None) -> str:
    """数据依据节（票 07）：行数/展示范围/所用表——全部确定性派生，零 token。"""
    shown = len(res.rows)
    if res.row_count > shown:
        line = f"共取到 {res.row_count} 行，本答案仅列示前 {shown} 行"
    elif shown:
        line = f"共取到 {shown} 行（全部列示）"
    else:
        line = "共取到 0 行（未查询到数据）"
    tables = used_tables(sql or "")
    if tables:  # 认不出来就省略该字段（诚实，不猜）
        line += f"；所用表：{'、'.join(tables)}"
    return line


def _evidence_caliber(state: dict) -> str:
    """兜底/失败态的口径说明：引用 evidence 命中项（载体 A evidence_terms，
    题面明示才有的原样摘录）。零 prompt——intent 只作展示消费，喂 prompt 纪律不破
    （test_generate_never_reads_intent 仍钉死 generate）。无命中项＝空串（节省略）。"""
    intent = state.get("intent")
    terms = intent.get("evidence_terms") if isinstance(intent, dict) else None
    terms = [str(t) for t in (terms or []) if str(t).strip()]
    return f"口径依据（题面摘录）：{'；'.join(terms)}" if terms else ""


def make_nodes(llm, tracer=None, settings: Settings | None = None, limiter=None,
               skip_respond: bool = False, on_event=None):
    """节点工厂：闭包注入 llm/tracer/settings/limiter，便于测试时替换假模型与配置。
    skip_respond：评测模式——成功路径不生成结论文本（判分只读 answer.sql 的执行结果），
    失败诚实汇报与回退重执行不受影响。产品路径（ask/Web）默认 False，全家桶保留。
    on_event（票 03）：节点级进度回调，帧＝{node, attempt, status}——attempt 为该时刻
    已入账的 SQL 尝试数（本轮未入账则停在上一计数，重试环上单调递增）；status 为
    后端单源生成的一行中文（start＝开跑，其余＝该步结果与人读细节，如执行失败带
    修复建议）。缺省 None 零发射、零包装，与现状逐行为一致。"""
    s = settings or FALLBACK_SETTINGS

    def _emit(node: str, attempt: int, status: str) -> None:
        if on_event is not None:
            on_event({"node": node, "attempt": attempt, "status": status})

    def _wrap(name: str, fn):
        """start 帧统一由包装层发（少一处节点内样板）；关态直接返回原函数。"""
        if on_event is None:
            return fn

        def wrapped(state: dict) -> dict:
            _emit(name, len(state.get("attempts", [])), "start")
            return fn(state)

        return wrapped

    def understand(state: dict) -> dict:
        # 载体 A（M5 票 02）：改写＋六字段意图同调产出，零新增调用；意图只入状态供
        # metric_match 消费（票 05），generate 不读它（尾段注入线④判负已拆，见 graph/intent.py）；
        # 解析失败＝回退纯原文＋intent None，不写 attempts、不烧重试预算（§3.7 既定降级语义）。
        # 票 05：L2 会话历史并进这次改写的 prompt（指代消解复用"补全指代"既有机制，零新增调用）
        text = timed_invoke(llm, understand_prompt(
            state["question"], state.get("evidence", ""),
            format_session_history(state.get("session_context"))),
            "understand", tracer, limiter)
        question, intent = parse_understand_response(text)
        attempt = len(state.get("attempts", []))
        if intent is None:
            _emit("understand", attempt, "解析失败，按原问题作答")
        else:
            _emit("understand", attempt, "理解完成")
        return {"original_question": state["question"], "question": question, "intent": intent}

    def _registry_for(db_path: str):
        """按库名寻址注册表（metrics/<db>.yaml）；无文件＝None（该库不进指标层）。
        存在但损坏＝RegistryError 上抛，由 run_question 外层守护收敛为诚实失败
        （票 03「不带病运行」——静默跳过会让坏文件冒充「未覆盖」骗过兜底 ≥ 基线条款）。"""
        p = Path(s.metrics_dir) / f"{Path(db_path).stem}.yaml"
        return load_registry(p) if p.is_file() else None

    def _miss(state: dict, reason: str) -> dict:
        """未命中统一出口：载荷显式清 None（整值覆盖纪律），零额外 LLM 消耗。"""
        if tracer is not None:
            tracer.log("metric_match", outcome="miss", reason=reason)
        _emit("metric_match", len(state.get("attempts", [])), f"未命中：{reason}")
        return {"matched_metric": None, "metric_note": None}

    def metric_match(state: dict) -> dict:
        """两级匹配→填槽→渲染（M5 票 05）。命中写 current_sql 直连 execute；
        任何未命中形态（无注册表/无意图/两级皆空/填槽失败）走兜底，宁漏勿错。"""
        metrics = _registry_for(state["db_path"])
        if metrics is None:
            return _miss(state, "该库无注册表文件（整节点跳过，零调用）")
        intent = state.get("intent")
        if not isinstance(intent, dict):
            # 票面消费契约：解析失败回退态＝全部题判未命中，行为与 metric_layer=False 一致
            return _miss(state, "意图解析失败回退态（intent=None）")
        m = match_metric(intent.get("metric_mention"), metrics)
        level = "L1"
        if m is None:  # 第一级确定性未中 → 第二级 LLM 复核整表（禁写 SQL，只判身份）
            level = "L2"
            text = timed_invoke(llm, metric_review_prompt(state["question"],
                                                          state.get("evidence", ""), metrics),
                                "metric_match", tracer, limiter)
            m = parse_metric_review(text, metrics)
        if m is None:
            return _miss(state, "两级匹配未命中（L2 判 NONE 或解析失败）")
        # ⑨ 闸·机制版（票 06）：极值/比较题形在 L1/L2 任一级命中都撤销——
        # 「谁最大」要的是明细排序答案，不是聚合口径；L2 指令只守得住 L2 那道门。
        if has_extreme_signal(state.get("original_question"), state.get("question"),
                              intent.get("metric_mention"), intent.get("output_form")):
            return _miss(state, f"⑨ 闸：题形含具名极值/比较，撤销 {m.name} 命中走兜底")
        fill = fill_slots(m, intent, today=_today())
        if not fill.ok:
            return _miss(state, f"{level} 命中 {m.name} 但填槽未过：{fill.reason}")
        if tracer is not None:
            tracer.log("metric_match", outcome="hit", metric=m.name, level=level)
        _emit("metric_match", len(state.get("attempts", [])), f"命中：{m.name}")
        return {"matched_metric": m.name, "metric_note": None,
                "current_sql": render_sql(m, fill.params), "last_error": None}

    def explore(state: dict) -> dict:
        conn = open_readonly(state["db_path"])
        try:
            ctx = build_schema_context(conn, state["question"], llm=llm, tracer=tracer,
                                       db_path=state["db_path"], limiter=limiter,
                                       sample_values=s.value_sampling)
        finally:
            conn.close()
        _emit("explore", len(state.get("attempts", [])), "取到 Schema")
        # matched_metric 显式清 None：兜底路线（含模板降级后进环）不留命中载荷
        # ——降级恰好一次的闸在此关闭；metric_note 保留（generate 可见＋respond 标注）
        return {"db_schema": ctx, "matched_metric": None}

    def generate(state: dict) -> dict:
        history = format_failure_history(state.get("attempts", []), state.get("verify_note"),
                                         state.get("metric_note"))
        # 票 05：L1 上轮 SQL 草稿进尾部（增量改写参考）——仅素材不进路由，
        # 沙箱/校验/账本三层零改动，草稿写法照走完整 execute＋verify
        prompt = sql_prompt(
            schema=state.get("db_schema", ""),
            evidence=state.get("evidence", ""),
            question=state["question"],
            history=history,
            draft=format_session_draft(state.get("session_context")),
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
                _emit("generate", len(attempts), f"提取失败：{err}")
                return {"current_sql": None, "last_error": err, "attempts": attempts,
                        "precise_candidates": None}
            _emit("generate", len(state.get("attempts", [])), "生成 SQL")
            return {"current_sql": sqls[0], "last_error": None, "precise_candidates": sqls}
        text = timed_invoke(llm, prompt, "generate", tracer, limiter)
        try:
            sql = extract_sql(str(text))
        except ValueError as e:
            attempts = list(state.get("attempts", []))
            attempts.append(SqlAttempt(sql="", error=str(e)))
            _emit("generate", len(attempts), f"提取失败：{str(e).splitlines()[0]}")
            return {"current_sql": None, "last_error": str(e), "attempts": attempts}
        _emit("generate", len(state.get("attempts", [])), "生成 SQL")
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
                _emit("execute", len(attempts), _exec_fail_status(err))
                return {"attempts": attempts, "result": None, "last_error": err,
                        "precise_candidates": None}
            if outcome.no_majority:
                attempts.append(SqlAttempt(sql="", error=NO_MAJORITY_ERROR))
                # 票决不能自证＝谈不上"执行成功"（评审收紧）：帧如实说"代表待强判"
                _emit("execute", len(attempts),
                      f"票决无多数派，取最大组代表 {outcome.winner_result.row_count} 行待校验")
            else:
                attempts.append(SqlAttempt(sql=outcome.winner_sql,
                                           row_count=outcome.winner_result.row_count))
                _emit("execute", len(attempts),
                      f"执行成功：{outcome.winner_result.row_count} 行")
            return {"attempts": attempts, "current_sql": outcome.winner_sql,
                    "result": outcome.winner_result, "last_error": None,
                    "precise_candidates": None}
        sql = state.get("current_sql")
        if not sql:
            # generate 阶段已失败：显式清掉上一轮残留的 result（整值覆盖语义下
            # "清除"必须显式返回），否则条件边①会拿陈旧 result 误走 verify 路径
            _emit("execute", len(state.get("attempts", [])), "无可执行 SQL，转重试")
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
            _emit("execute", len(attempts), _exec_fail_status(msg))
            out = {"attempts": attempts, "result": None, "last_error": msg}
            matched = state.get("matched_metric")
            if matched:
                # 模板 SQL 失败：入账后降级兜底（路由判 matched_metric → explore）
                out["metric_note"] = f"指标模板「{matched}」执行失败：{msg.splitlines()[0]}"
            return out
        attempts.append(SqlAttempt(sql=sql, row_count=res.row_count))
        _emit("execute", len(attempts), f"执行成功：{res.row_count} 行")
        return {"attempts": attempts, "result": res, "last_error": None}

    def verify(state: dict) -> dict:
        res = state.get("result")
        if res is None:
            # 执行已失败：路由直接走兜底，无需校验（防御分支——现路由不可达，帧照发不悬停）
            _emit("verify", len(state.get("attempts", [])), "跳过校验（无结果）")
            return {"verify_note": None}
        last = state.get("attempts", [])[-1] if state.get("attempts") else None
        attempt = len(state.get("attempts", []))
        if last is not None and not last.sql and last.error == NO_MAJORITY_ERROR:
            # 票决不能自证：无多数派一律判可疑（不计正确路线）→ 有预算重试/耗尽带标注
            _emit("verify", attempt, f"可疑：{NO_MAJORITY_ERROR}")
            return {"verify_note": NO_MAJORITY_ERROR}
        verdict = verify_result(state["question"], state.get("current_sql") or "", res)
        if verdict.passed:
            _emit("verify", attempt, "校验通过")
            return {"verify_note": None}
        _emit("verify", attempt, f"可疑：{verdict.reason}")
        out = {"verify_note": verdict.reason}
        matched = state.get("matched_metric")
        if matched:
            # 命中但结果可疑：留降级原因（进失败历史与 respond 标注），路由经 explore 降回兜底
            out["metric_note"] = f"指标模板「{matched}」的结果未通过自动校验：{verdict.reason}"
        return out

    def respond(state: dict) -> dict:
        res = state.get("result")
        sql = state.get("current_sql")
        attempts = state.get("attempts", [])
        fallback_note = None
        # 评测路径字段（票 05）：matched_metric 非空＝模板作答；matched 已清但 metric_note
        # 在＝模板降级后由兜底作答（fell_back）；二者皆无＝纯兜底。
        matched = state.get("matched_metric")
        metric_note = state.get("metric_note")
        path_name = "metric" if matched else "fallback"
        fell_back = matched is None and metric_note is not None
        route_kwargs = {"path": path_name, "metric_name": matched,
                        "template_fell_back": fell_back}
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
            # 票 07：仍按三节组装——数据依据如实记无结果集，模板降级原因进校验节
            if attempts:
                lines = [f"未能完成查询（共尝试 {len(attempts)} 次）："]
                for i, a in enumerate(attempts, 1):
                    err = (a.error or "未知错误").splitlines()[0]
                    lines.append(f"{i}. {a.sql or '（未提取到 SQL）'} → {err}")
                conclusion = "\n".join(lines)
            else:
                conclusion = f"未能完成查询：{state.get('last_error') or '未知错误'}"
            notes = [metric_note] if metric_note else []
            # 失败态不走命中指标口径（模板没答出来，展示其口径会误导"已由该口径作答"）
            _emit("respond", len(attempts), "如实报失败")
            return {
                "answer": Answer(
                    conclusion=compose_conclusion(
                        conclusion,
                        basis="无成功执行的查询，无可用结果集（尝试明细见结论）",
                        caliber=_evidence_caliber(state), notes=notes),
                    sql=sql,
                    result=None,
                    failed=True,
                    error_summary=state.get("last_error"),
                    **route_kwargs,
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
        # 口径说明节（票 07）：命中→注册表零 token 引用口径与血缘；查不到→evidence 命中项
        caliber = ""
        if matched:
            try:
                registry = _registry_for(state["db_path"])
            except RegistryError:
                registry = None  # 运行中途注册表损坏：已取回的真实数据不受连累，血缘诚实省略
            m = next((x for x in (registry or []) if x.name == matched), None)
            if m is not None:
                caliber = lineage_text(m)
        if not caliber:
            caliber = _evidence_caliber(state)
        notes = []
        note = state.get("verify_note")
        if note:  # 可疑但预算耗尽：数据真实，如实呈现＋标注（不是假失败）
            notes.append(f"该结果未通过自动校验：{note}")
        if res.truncated:  # 截断标注并入校验节（票 07 条款④：不另开新节）
            # 不重复数据依据节的行数（评审收紧：同一事实两处表述会漂移）——
            # 本条只陈述数据依据给不了的事实：结论实际只看了预览行
            notes.append(f"结果已截断：结论仅基于前 {len(preview)} 行预览生成")
        if fallback_note:  # 回退作答：数据真实，如实标注来源
            notes.append(fallback_note)
        if fell_back and metric_note and not fallback_note:  # 模板降级由兜底 SQL 作答，如实标注
            # fallback_note 已置＝答案实为 _last_good_sql 复活的（可能是模板本身），
            # 此时再称"由兜底路径生成"会与实际数据矛盾（数字诚实），交回退标注说明即可
            notes.append(f"{metric_note}，最终答案由兜底路径生成")
        _emit("respond", len(attempts), "作答完成")
        return {"answer": Answer(
            conclusion=compose_conclusion(conclusion, _basis_line(res, sql), caliber, notes),
            sql=sql, result=res, failed=False, **route_kwargs)}

    return {
        "understand": _wrap("understand", understand),
        "metric_match": _wrap("metric_match", metric_match),
        "explore": _wrap("explore", explore),
        "generate": _wrap("generate", generate),
        "execute": _wrap("execute", execute),
        "verify": _wrap("verify", verify),
        "respond": _wrap("respond", respond),
    }
