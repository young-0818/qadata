"""图节点：understand → explore → generate → execute → verify → respond。

M5 指标层（metric_match 可选节点）已于 2026-09-21 退役（ADR-0007，票 09 段 B）——
六节点自纠错状态机为唯一形态。"""
import re
import time

from langgraph.types import interrupt

from qadata.config import FALLBACK_SETTINGS, Settings
from qadata.graph.error_hints import error_hint
from qadata.graph.gssc import (
    assemble,
    fuse_ledger_fields,
    gather_generate,
    gather_respond,
    gather_understand,
)
from qadata.graph.intent import parse_understand_response
from qadata.graph.precise import NO_MAJORITY_ERROR, run_precise_batch
from qadata.graph.prompts import (
    SUPPLEMENT_MARK,
    brief_head,
    compose_conclusion,
    compose_supplement,
    strip_conclusion_prefix,
)
from qadata.graph.verify import verify_result
from qadata.llm.tracing import timed_invoke, timed_stream, tool_frame, tool_start_frame
from qadata.tools.db import open_readonly
from qadata.tools.executor import execute_sql
from qadata.tools.schema import build_schema_context
from qadata.tools.sqlguard import used_tables
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




def make_nodes(llm, tracer=None, settings: Settings | None = None, limiter=None,
               skip_respond: bool = False, on_event=None, hitl: bool = False,
               recall=None, table_recall=None, value_link=None, knowledge_recall=None):
    """节点工厂：闭包注入 llm/tracer/settings/limiter，便于测试时替换假模型与配置。
    skip_respond：评测模式——成功路径不生成结论文本（判分只读 answer.sql 的执行结果），
    失败诚实汇报与回退重执行不受影响。产品路径（ask/Web）默认 False，全家桶保留。
    on_event（票 03）：节点级进度回调，帧＝{node, attempt, status}——attempt 为该时刻
    已入账的 SQL 尝试数（本轮未入账则停在上一计数，重试环上单调递增）；status 为
    后端单源生成的一行中文（start＝开跑，其余＝该步结果与人读细节，如执行失败带
    修复建议）。缺省 None 零发射、零包装，与现状逐行为一致。
    hitl（M8 票 03 owner 改判 2026-09-16）：澄清走经典 HITL——understand 节点内
    interrupt() 暂停等人类（需调用侧配好 checkpointer＋thread_id），缺省 False＝
    澄清落 answer 直达 END（无会话/CLI/评测形态）。
    recall（M9 票 06）＝例题库召回回调（question→注入块），只被 generate 经装配器
    Select 格消费；缺省 None＝逐字节现状（CLI/eval 零挂接，on_event 纪律同族）。
    table_recall（M10 票 01）＝表卡粗召回调（question→候选表名 list|None），只被
    explore 的大库分支消费（小库根本不读索引文件）；缺省 None＝现状一把梭逐字节
    一致——账本与降级在回调闭包内自持（retrieval/cards），本层零知情。
    value_link（M10 票 03）＝值链查询调（(question, intent, 入选表)→纸条块，""＝
    不贴），被 explore 消费进 schema 上下文（**小库大库都触发**——值链与表卡相反）；
    搭车料＝state["intent"]（understand 现成输出，零新增生成调用；None＝本轮无纸条
    ——spec §二 Q5 成文）。账本/降级/memo 在闭包内自持（retrieval/values），本层
    零知情；缺省 None＝逐字节现状（on_event/table_recall 末位纪律同族、不进状态键）。
    knowledge_recall（M10 票 05）＝口径字典召回回调（question→字典块，""＝不注入），
    只被 generate 经装配器 Select 格第四槽消费（消解后题面＝slots["question"]）；
    账本/降级/memo/过期闸在闭包内自持（retrieval/knowledge），本层零知情；
    缺省 None＝逐字节现状（recall 末位纪律同族、不进状态键）。

    M8 票 06 帧喂厚：结果帧追加 ok/duration_ms/tokens_in/tokens_out（start 帧与
    末帧 answer 契约零动）；tools 层子步骤发 kind:"tool" 帧（explore 内
    list_tables/get_schema/…、execute 内 execute_sql）。token 流走 timed_invoke 的
    sink（_step_box）＝逐调用累计，图与 tools 每请求各建一份（build_graph 在
    run_question 内），闭包无跨请求串扰。缺省 None 关态零发逐行为一致不变。"""
    s = settings or FALLBACK_SETTINGS
    # 票 06：当前步的计时起点与 token 累计（_wrap 每次进入重置；仅 on_event 在时读写）
    _step_box = {"t0": 0.0, "tin": 0, "tout": 0}
    sink = _step_box if on_event is not None else None

    def _ms(t0: float) -> int:
        return round((time.perf_counter() - t0) * 1000)

    def _emit(node: str, attempt: int, status: str, ok: bool = True) -> None:
        """结果帧（该步收口）：票 06 起带 ok/耗时/token——ok 二值＝红绿点语义，
        降级/可疑/未命中不改红（那不是失败，status 文案如实说）。"""
        if on_event is not None:
            on_event({"node": node, "attempt": attempt, "status": status, "ok": ok,
                      "duration_ms": _ms(_step_box["t0"]),
                      "tokens_in": _step_box["tin"], "tokens_out": _step_box["tout"]})

    def _fuse(node: str):
        """保险丝降级入账回调（M9 票 04）：账本一行（budget_fuse，无 token 标记＝
        不计为 LLM 调用，recall 行先例）＋直播 tool 胶囊（控制台/回放/
        trace 三出口同帧形，票 01 镜像零改动承接）。仅在真压缩时被装配器调用。"""
        def cb(info: dict) -> None:
            if tracer is not None:
                tracer.log("budget_fuse", scenario=node, **fuse_ledger_fields(info))
            if on_event is not None:
                on_event(tool_frame(node, "budget_fuse", time.perf_counter()))
        return cb

    def _tool(node: str, name: str, t0: float, ok: bool = True) -> None:
        """tool 子事件帧（票 06）：图不是 tool loop（架构不动裁决），但 tools 层的
        真实子步骤值得有自己的胶囊——绿点成功/红点失败，duration 各自计时。
        形状经 tool_frame 单源（与 explore 子步骤同源，防帧形两处漂移）。"""
        if on_event is not None:
            on_event(tool_frame(node, name, t0, ok))

    def _tool_start(node: str, name: str) -> None:
        """tool start 帧（实时工具链）：慢操作开跑即发活口，前端当场成行转圈——
        execute_sql 撞 5s 超时线的等待期不再是页面装死。收口帧形状零动。"""
        if on_event is not None:
            on_event(tool_start_frame(node, name))

    def _wrap(name: str, fn):
        """start 帧统一由包装层发（少一处节点内样板）＋步盒重置；关态直接返回原函数。"""
        if on_event is None:
            return fn

        def wrapped(state: dict) -> dict:
            _step_box["t0"] = time.perf_counter()
            _step_box["tin"] = _step_box["tout"] = 0
            on_event({"node": name, "attempt": len(state.get("attempts", [])),
                      "status": "start"})
            return fn(state)

        return wrapped

    def _llm(prompt: str, node: str) -> str:
        """M8 票 08：有进度流（on_event 在场）时走流式旁路把思考增量推 thinking 帧，
        收口 token 与原路同形入账；关态（CLI/eval）恒 timed_invoke——逐字节一致。
        仅 understand/generate 用（题面里那 12~30s 死寂最该「看着它想」），其余节点照旧。
        M9 票 01 窄例外：qadata_no_stream 标记（obs-only 镜像、无直播消费者）不开流式——
        观测出口不得改变 LLM 调用形态（eval 开配对轮与历史可比的前提）。"""
        if on_event is not None and not getattr(on_event, "qadata_no_stream", False):
            return timed_stream(llm, prompt, node, tracer, limiter, sink=sink,
                                on_event=on_event)
        return timed_invoke(llm, prompt, node, tracer, limiter, sink=sink)

    def _knowledge_caliber(question: str) -> str:
        """口径说明节（ADR-0008 字典单通道）＝本题字典召回块各条首行摘要——
        memo 命中零新调用零新状态键；未挂字典/无召回＝空串（节省略，逐字节现状）。"""
        if knowledge_recall is None:
            return ""
        heads = [ln[2:] for ln in knowledge_recall(question).splitlines()
                 if ln.startswith("- ")]
        return (f"口径字典片段（按题面检索所得，非钦定全文）：{'；'.join(heads)}"
                if heads else "")

    def understand(state: dict) -> dict:
        # 载体 A（M5 票 02 立、M9 票 03 值链接手）：改写＋四字段意图同调产出，零新增调用；
        # 意图入状态供值链搭车抽词（M10 票 03），generate 不读它（尾段注入线④判负已拆，
        # 见 graph/intent.py）；解析失败＝回退纯原文＋intent None，不写 attempts、不烧重试预算。
        # 票 05：L2 会话历史并进这次改写的 prompt（指代消解复用"补全指代"既有机制，零新增调用）
        # 票 03（M8，默认关）：同一改写调用兼产澄清问（三元组，宁空勿造）——保险丝出口，
        # 关态与今日逐字节一致（prompt 不追加指令段、本节点不写 answer、路由 map 不含 END）。
        text = _llm(assemble("understand",
                             gather_understand(state, clarify=s.clarification,
                                               decompose=s.decompose),
                             on_compress=_fuse("understand")),
                    "understand")
        question, intent, clarification, steps = parse_understand_response(text)
        attempt = len(state.get("attempts", []))
        if (s.clarification and clarification
                and SUPPLEMENT_MARK not in state["question"]):
            # 防循环＝双闸：续轮题面带「补充说明：」标记时指令段不加（prompt 侧）＋
            # 此处机制版复闸（模型违令产出也不消费——M5 ⑨闸教训：指令守不住的代码兜），
            # 续轮必带标记由 compose_supplement 单源保证（HITL 恢复态与前端合成同式）。
            if hitl:
                # 经典 HITL（owner 改判 2026-09-16）：节点内 interrupt() 暂停等人类——
                # 首跑即在此抛出（invoke 返回 __interrupt__ 面，暂停＝正常返回、零线程挂等）；
                # 人类补充经 Command(resume) 到达时本节点从头重放、interrupt() 直接返回补充文本。
                # 重放＝understand 共 2 次 LLM 调用（langgraph 节点副作用重放的既定代价，
                # 仍省于跨请求重来：澄清往返 4 call < 前端合成 6 call）。
                supplement = str(interrupt({"clarification": clarification}) or "").strip()
                _emit("understand", attempt, "补充已到，继续作答")
                return {"original_question": state["question"],
                        "question": compose_supplement(state["question"], clarification,
                                                       supplement),
                        "intent": intent}
            _emit("understand", attempt, "口径缺失过大，先澄清一句")
            return {"original_question": state["question"], "question": question,
                    "intent": intent,
                    "answer": Answer(conclusion=clarification, clarification=clarification)}
        if s.decompose and steps:
            # 分治消费闸（M11 票 03，clarification 同款双闸结构、且在其后——澄清是保险丝
            # 优先于拆分）：开关开且形状过关才拆，首步即题面、plan 记全步与进度；
            # 关态模型违令产出 steps 也绝不消费（姊妹钉）。
            _emit("understand", attempt, f"理解完成（分 {len(steps)} 步）")
            return {"original_question": state["question"], "question": steps[0],
                    "intent": intent, "plan": {"steps": steps, "i": 0}}
        if intent is None:
            _emit("understand", attempt, "解析失败，按原问题作答")
        else:
            _emit("understand", attempt, "理解完成")
        return {"original_question": state["question"], "question": question, "intent": intent}

    def explore(state: dict) -> dict:
        conn = open_readonly(state["db_path"])
        # 票 03：纸条调在本节点绑定搭车料（question＋intent），build_schema_context
        # 只见「入选表→块」的窄形——扫描在 retrieval 闭包内按题面 memo，重试环/
        # 精准模式不翻倍向量调用。
        # M11 票 03：分治轮选表看**原全题**（后续步可能引用别的表，首步题面选窄＝漏料）；
        # 关态无 plan 键＝表达式与今日逐字节一致。
        explore_q = ((state.get("original_question") or state["question"])
                     if state.get("plan") else state["question"])
        vl = ((lambda names: value_link(state["question"], state.get("intent"), names))
              if value_link is not None else None)
        try:
            ctx = build_schema_context(conn, explore_q, llm=llm, tracer=tracer,
                                       db_path=state["db_path"], limiter=limiter,
                                       sample_values=s.value_sampling,
                                       on_event=on_event, sink=sink,
                                       table_recall=table_recall, value_link=vl)
        finally:
            conn.close()
        _emit("explore", len(state.get("attempts", [])), "取到 Schema")
        return {"db_schema": ctx}

    def generate(state: dict) -> dict:
        # 票 05：L1 上轮 SQL 草稿进尾部（增量改写参考）——仅素材不进路由，
        # 沙箱/校验/账本三层零改动，草稿写法照走完整 execute＋verify
        # M9 票 03：失败历史（状态）＋草稿（记忆）等素材收编进 GSSC 出口，逐字节同旧路
        # M11 票 03 步序推进：上一步跑成（result 在场）且还有后续步 → 摘新步题面＋把上步
        # 回执（问题/SQL/结果头）冻结进 plan.prev（execute 失败清 result 也不丢桥——重试
        # 看得见上步）。判据可靠：中间步成功从不进 verify，verify 可疑重试时 plan 已尽
        # （i+1==len）不触发推进；提取失败重试 result 恒 None 同样不误进。
        plan = state.get("plan")
        advanced: dict = {}
        if plan and plan.get("steps") and state.get("result") is not None \
                and plan["i"] + 1 < len(plan["steps"]):
            prev = {"q": plan["steps"][plan["i"]],
                    "sql": state.get("current_sql"),
                    "head": brief_head(state["result"])}
            plan = {**plan, "i": plan["i"] + 1, "prev": prev}
            advanced = {"plan": plan, "question": plan["steps"][plan["i"]]}
            state = {**state, "plan": plan, "question": plan["steps"][plan["i"]]}
        prompt = assemble("generate", gather_generate(state), on_compress=_fuse("generate"),
                          recall=recall, knowledge_recall=knowledge_recall)
        if s.precise_candidates > 1:
            # 精准模式：同一 prompt 连打 K 发（temperature 由 build_llm 按候选数切换，
            # 多样性已探针验证）；提取失败的候选丢弃，全灭走提取失败入账路径
            sqls, extract_fails = [], 0
            for _ in range(s.precise_candidates):
                text = _llm(prompt, "generate")
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
                _emit("generate", len(attempts), f"提取失败：{err}", ok=False)
                return {"current_sql": None, "last_error": err, "attempts": attempts,
                        "precise_candidates": None, **advanced}
            _emit("generate", len(state.get("attempts", [])), "生成 SQL")
            return {"current_sql": sqls[0], "last_error": None,
                    "precise_candidates": sqls, **advanced}
        text = _llm(prompt, "generate")
        try:
            sql = extract_sql(str(text))
        except ValueError as e:
            attempts = list(state.get("attempts", []))
            attempts.append(SqlAttempt(sql="", error=str(e)))
            _emit("generate", len(attempts), f"提取失败：{str(e).splitlines()[0]}", ok=False)
            return {"current_sql": None, "last_error": str(e), "attempts": attempts,
                    **advanced}
        _emit("generate", len(state.get("attempts", [])), "生成 SQL")
        return {"current_sql": sql, "last_error": None, **advanced}

    def execute(state: dict) -> dict:
        candidates = state.get("precise_candidates")
        if candidates and len(candidates) > 1:
            # 票决轮（M4-C）：批量=一轮账本，attempts 只记一条；用完显式清载荷键
            _tool_start("execute", "execute_sql_batch")
            t0 = time.perf_counter()
            outcome = run_precise_batch(state["db_path"], candidates,
                                        max_rows=s.max_rows, timeout_s=s.sql_timeout_s,
                                        tracer=tracer)
            _tool("execute", "execute_sql_batch", t0, ok=outcome.winner_sql is not None)
            attempts = list(state.get("attempts", []))
            if outcome.winner_sql is None:
                err = f"精准模式 {len(candidates)} 个候选全部执行失败"
                if outcome.fail_excerpt:
                    err += f"：{outcome.fail_excerpt}"
                attempts.append(SqlAttempt(sql="", error=err))
                _emit("execute", len(attempts), _exec_fail_status(err), ok=False)
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
            _emit("execute", len(state.get("attempts", [])), "无可执行 SQL，转重试", ok=False)
            return {"result": None}
        attempts = list(state.get("attempts", []))
        _tool_start("execute", "execute_sql")
        t0 = time.perf_counter()
        try:
            res = execute_sql(
                state["db_path"], sql,
                max_rows=s.max_rows, timeout_s=s.sql_timeout_s,
            )
        except Exception as e:  # noqa: BLE001 自纠错账本：任何执行失败都要入账供重试
            _tool("execute", "execute_sql", t0, ok=False)  # 失败红点胶囊（截图语义）
            msg = str(e)
            attempts.append(SqlAttempt(sql=sql, error=msg))
            _emit("execute", len(attempts), _exec_fail_status(msg), ok=False)
            return {"attempts": attempts, "result": None, "last_error": msg}
        _tool("execute", "execute_sql", t0)
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
        return {"verify_note": verdict.reason}

    def respond(state: dict) -> dict:
        res = state.get("result")
        sql = state.get("current_sql")
        attempts = state.get("attempts", [])
        # 执行失败耗尽＝判死（owner 裁 2026-09-22）：最后一版跑不通就诚实失败，
        # 不回滚旧答案救场——原「答案稳定性回退」整层撤除（M3 立、活了六个里程碑）
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
            _emit("respond", len(attempts), "如实报失败", ok=False)
            return {
                "answer": Answer(
                    conclusion=compose_conclusion(
                        conclusion,
                        basis="无成功执行的查询，无可用结果集（尝试明细见结论）",
                        caliber=_knowledge_caliber(state["question"]), notes=[]),
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
            text = timed_invoke(
                llm,
                assemble("respond", gather_respond(
                    state, result=res, sql=sql,
                    rows_table=_format_preview(res.columns, preview),
                    n=len(preview)), on_compress=_fuse("respond")),
                "respond",
                tracer,
                limiter,
                sink=sink,
            )
            conclusion = strip_conclusion_prefix(str(text))
        # 口径说明节（M5 票 07 立；ADR-0008 改字典单通道）：本题字典召回块首行摘要
        caliber = _knowledge_caliber(state["question"])
        notes = []
        note = state.get("verify_note")
        if note:  # 可疑但预算耗尽：数据真实，如实呈现＋标注（不是假失败）
            notes.append(f"该结果未通过自动校验：{note}")
        if res.truncated:  # 截断标注并入校验节（票 07 条款④：不另开新节）
            # 不重复数据依据节的行数（评审收紧：同一事实两处表述会漂移）——
            # 本条只陈述数据依据给不了的事实：结论实际只看了预览行
            notes.append(f"结果已截断：结论仅基于前 {len(preview)} 行预览生成")
        _emit("respond", len(attempts), "作答完成")
        return {"answer": Answer(
            conclusion=compose_conclusion(conclusion, _basis_line(res, sql), caliber, notes),
            sql=sql, result=res, failed=False)}

    return {
        "understand": _wrap("understand", understand),
        "explore": _wrap("explore", explore),
        "generate": _wrap("generate", generate),
        "execute": _wrap("execute", execute),
        "verify": _wrap("verify", verify),
        "respond": _wrap("respond", respond),
    }
