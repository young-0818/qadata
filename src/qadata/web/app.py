"""M7-rev2 票 02.5/03/05 web 薄壳：应用工厂＋DI＋智能体面＋进度流＋会话＋前端同源服务。

唯一入口不破：/api/ask 与 /api/ask/stream 只经 run_question（沙箱四层无旁路），
本模块不建模型、不读密钥、不碰 sqlite——llm/settings/agents（AgentStore）由
调用方注入（serve 或测试的 ScriptedLLM＋tmp 存储）。智能体＝一只目录
（data/agents/<id>/），数据源上传唯一路、业务知识引用态读取期派生；schema 摘要
经 open_readonly 只读打开（导入落盘零建连、读取展示走沙箱①层——三层钉测见契约
测试）。/api/ask 响应 12 字段冻结不动（spec 响应契约：改形状＝跨票改卷；
session_id 自票 05 出真值——请求带会话则回显，单轮请求照旧 null）；票 04 经
owner 裁决**新增可选字段 chart**（图型判定＝web/charts.py 规则纯函数，一判双达
两端点）；M8 票 03 经 owner 立项**新增可选字段 clarification**（澄清回合，默认关）
——共 14 字段。
票 03：/api/ask/stream 以 SSE 直播节点级进度帧（node/attempt/status 三字段
起步；M8 票 06 喂厚＝结果帧加 ok/duration_ms/tokens_in/tokens_out、tools 子步骤
kind:"tool" 帧，start 帧与末帧契约零动）＋末帧 event:answer（即契约本体，与
/api/ask 同源）。
票 05：多轮会话落盘（web/sessions.py，owner 裁决 2026-09-14 推翻"内存态"）——
请求可选 session_id 装载三层记忆（图侧零新增调用）、问完落盘一轮；在途锁键升格
session_id（单轮请求维持 agent 级）；历史会话端点供侧栏列表与重开回放（回放＝
问答本体，自纠错 trail 属现场观察不入档）。口径优先级照旧＝请求显式 > 智能体
业务知识 > 空（会话级叠加框经 owner 裁 2026-09-15 撤销，票 05 修订见 spec）。
"""
import json
import queue
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.memory import MemorySaver
from pydantic import BaseModel, Field

from qadata.config import Settings
from qadata.graph.build import resume_question, run_question
from qadata.graph.prompts import compose_supplement
from qadata.tools.db import open_readonly
from qadata.tools.schema import list_tables
from qadata.types import Answer
from qadata.web.agents import (
    AgentMeta,
    AgentNotFound,
    AgentStore,
    AgentStoreError,
)
from qadata.web.charts import decide_chart
from qadata.web.feedback import FeedbackError, append_vote, latest_votes
from qadata.web.sessions import (
    Session,
    SessionNotFound,
    SessionStore,
    SessionStoreError,
    append_turn,
    build_session_context,
)

# 前端未构建时的诚实占位页（侦察笔记：别白屏，写明构建命令）
_PLACEHOLDER_PAGE = """<!doctype html>
<html lang="zh-CN">
<meta charset="utf-8">
<title>问数 · 前端未构建</title>
<body style="font-family: system-ui; max-width: 40rem; margin: 4rem auto">
<h1>前端尚未构建</h1>
<p>API 已在服务（<code>GET /api/agents</code> / <code>POST /api/ask</code>）。要看到页面，请在仓库根目录执行：</p>
<pre style="background:#f4f4f5; padding:.75rem; border-radius:.6rem"><code>cd web
npm install
npm run build</code></pre>
<p>构建完成后刷新本页。</p>
</body>
</html>
"""


class AskRequest(BaseModel):
    agent_id: str
    question: str = Field(min_length=1)
    evidence: str = ""
    # 票 05：会话 id（客户端生成 hex12，懒建档——"＋ 新建会话"＝换 id、下一问开新档）；
    # 缺省 None＝单轮关态（三层记忆零注入，与票 04 逐行为一致）
    session_id: str | None = None
    # M8 票 03 改判（经典 HITL）：带 pending 澄清时显式放弃续答、本条按新话题问；
    # 无 pending 时无害忽略（请求面放宽不动响应契约）
    discard_pending: bool = False


class AgentCreateRequest(BaseModel):
    name: str
    description: str = ""


class FeedbackRequest(BaseModel):
    """M8 票 04：一票评价。ts＝被评轮的落盘时刻（回放接口给出），vote ∈ up/down。
    反馈只在会话面存活（旁挂票档）——不进 AskResponse 契约、不进记忆与路由。"""
    session_id: str
    ts: str
    vote: str


class AgentPatchRequest(BaseModel):
    # 全部可选：PATCH 只动显式传入的字段（model_fields_set 即"传了哪些"）
    name: str | None = None
    description: str | None = None
    evidence: str | None = None
    metrics_ref: str | None = None
    preset_questions: list[str] | None = None


def answer_to_payload(answer: Answer, session_id: str | None = None) -> dict[str, Any]:
    """Answer/QueryResult → 契约 JSON（票 01 冻结 12 字段＋票 04 新增可选 chart＋
    M8 票 03 新增可选 clarification，共 14 字段；无结果集时行列与图型如实 null）。

    session_id（票 05）＝请求所带会话 id 的回显；单轮请求（无 session_id）照旧 null。
    chart＝decide_chart 规则纯函数对结果集形态的一次裁决（折线/柱/大数卡，
    判不了即 null＝表格）；两端点同经本函数，一判双达不漂移。
    clarification（M8 票 03，开关关恒 null）＝澄清轮的问句本体——非失败、非答案；
    owner 改判 2026-09-16＝经典 HITL：带会话时此为暂停面（checkpoint 存档，下一条
    消息自动续答），单轮/CLI 形态仍为直达 END；澄清轮不落盘、字段形状两端点一致。
    """
    res = answer.result
    columns = list(res.columns) if res else None
    rows = [list(row) for row in res.rows] if res else None
    return {
        "conclusion": answer.conclusion,
        "sql": answer.sql,
        "columns": columns,
        "rows": rows,
        "truncated": res.truncated if res else None,
        "elapsed_ms": res.elapsed_ms if res else None,
        "failed": answer.failed,
        "error_summary": answer.error_summary,
        "path": answer.path,
        "metric_name": answer.metric_name,
        "template_fell_back": answer.template_fell_back,
        "session_id": session_id,
        "chart": decide_chart(columns or [], rows or []) if res else None,
        "clarification": answer.clarification,
    }


def create_app(agents: AgentStore, llm=None, settings: Settings | None = None,
               static_dir: str | Path | None = None,
               tracer=None) -> FastAPI:
    """应用工厂：agents（AgentStore）必填、llm/settings/static_dir/tracer 注入——
    契约测试注 tmp 存储＋假模型，工厂不摸文件系统做装配（评审收紧：删 None 兜底，
    不给"自己造真目录 store"留后路）。

    llm=None 时逐题经 run_question 内部 build_llm（需密钥）；生产路径由 serve 注入
    共享实例。static_dir 默认 web/dist 沿仓库根 cwd 约定（与 TRACE_PATH 同款）。
    """
    store = agents
    sessions = SessionStore(store)  # 票 05：会话住智能体目录内，删智能体连带清会话
    dist = Path(static_dir) if static_dir is not None else Path("web/dist")
    app = FastAPI(title="qadata-web", docs_url=None, redoc_url=None)

    # M8 票 03 改判（经典 HITL，owner 裁 2026-09-16）：澄清暂停态＝图侧 interrupt()
    # 存档于 checkpointer，续答＝Command(resume) 原 thread 续跑。pending 指针住**进程内**
    # （key=agent:sid）——不入会话档、不开运行时写入口，票 09 的 405 钉与"写前重载合并
    # 已裁撤"两条裁决原样存活（当初"三重改卷"之忧，被指针放对位置整个绕开）。
    # ponytail: MemorySaver＝重启丢在途澄清（单进程自托管约定与在途锁同款）；
    # 升级路径＝langgraph-checkpoint-sqlite（新依赖＋data/ 落一档），多进程/长跑需要时再装。
    checkpoint = MemorySaver() if (settings is not None and settings.clarification) else None

    @dataclass
    class _PendingAsk:
        thread: str  # checkpoint thread 号（agent:sid:随机尾——每问一新 thread，防终态串档）
        question: str  # 澄清前的原始问题（合成与回放展示用）
        ask: str  # 澄清问句

    _pending: dict[str, _PendingAsk] = {}  # 读写均发生在在途锁内（单进程约定）

    def _bad(e: AgentStoreError | SessionStoreError) -> HTTPException:
        if isinstance(e, (AgentNotFound, SessionNotFound)):
            return HTTPException(status_code=404, detail=str(e))
        return HTTPException(status_code=400, detail=str(e))

    def _datasource_info(meta: AgentMeta) -> dict[str, Any]:
        path = store.datasource_path(meta)
        if path is None:
            return {"has_file": False, "table_count": None, "error": None}
        try:
            conn = open_readonly(str(path))  # 沙箱①层：摘要展示也走唯一只读入口
            try:
                return {"has_file": True, "table_count": len(list_tables(conn)),
                        "error": None}
            finally:
                conn.close()
        except Exception as e:  # noqa: BLE001 摘要展示层：打不开一律如实上报不装正常
            # （含 sqlite3 系异常——web 包 import 纪律不引 sqlite3，捕获面在此宽为有意为之）
            return {"has_file": True, "table_count": None, "error": str(e)}

    def _detail(meta: AgentMeta) -> dict[str, Any]:
        try:
            kb, kb_err = store.effective_evidence(meta), None
        except AgentStoreError as e:
            kb, kb_err = "", str(e)
        return {"id": meta.id, "name": meta.name, "description": meta.description,
                "evidence": meta.evidence, "metrics_ref": meta.metrics_ref,
                "business_knowledge": kb, "business_knowledge_error": kb_err,
                "preset_questions": list(meta.preset_questions),
                "datasource": _datasource_info(meta)}

    # ── 模型配置：只读卡（可写化＝后手裁决；.env 是模型唯一真源）──────

    @app.get("/api/model")
    def model_card() -> dict[str, Any]:
        return {"model": settings.model if settings else None,
                "source": "环境 .env → QADATA_MODEL", "writable": False}

    @app.get("/api/metrics-registries")
    def metric_registries() -> dict[str, list[str]]:
        return {"registries": store.registries()}

    # ── 智能体 CRUD（真空启动：初始为空列表）────────────────────────

    @app.get("/api/agents")
    def list_agents() -> dict[str, list[dict[str, Any]]]:
        try:
            metas = store.all()
        except AgentStoreError as e:
            raise _bad(e) from None
        return {"agents": [{"id": m.id, "name": m.name, "description": m.description,
                            "has_datasource": store.datasource_path(m) is not None}
                           for m in metas]}

    @app.post("/api/agents")
    def create_agent(req: AgentCreateRequest) -> dict[str, Any]:
        try:
            meta = store.create(req.name, req.description)
        except AgentStoreError as e:
            raise _bad(e) from None
        return _detail(meta)

    @app.get("/api/agents/{agent_id}")
    def get_agent(agent_id: str) -> dict[str, Any]:
        try:
            return _detail(store.get(agent_id))
        except AgentStoreError as e:
            raise _bad(e) from None

    @app.patch("/api/agents/{agent_id}")
    def patch_agent(agent_id: str, req: AgentPatchRequest) -> dict[str, Any]:
        try:
            return _detail(store.patch(agent_id, **req.model_dump(exclude_unset=True)))
        except AgentStoreError as e:
            raise _bad(e) from None

    @app.delete("/api/agents/{agent_id}")
    def delete_agent(agent_id: str) -> dict[str, bool]:
        try:
            store.delete(agent_id)
        except AgentStoreError as e:
            raise _bad(e) from None
        return {"ok": True}

    @app.post("/api/agents/{agent_id}/datasource")
    async def upload_datasource(agent_id: str, request: Request,
                                name: str = Query(..., description="原始文件名（仅用于扩展名白名单）"),
                                ) -> dict[str, bool]:
        # 只搬字节进智能体目录：本端点全程零建连（连 _detail 都不走），
        # 库能不能打开由详情摘要/问答链路经 open_readonly 如实回答
        try:
            store.store_datasource(agent_id, await request.body(), name)
        except AgentStoreError as e:
            raise _bad(e) from None
        return {"ok": True}

    # ── 票 05：会话面（侧栏列表／重开回放）──────────────────────────────
    # 会话懒建档＝首问落盘才建文件；回放只渲问答本体（answer 即契约 payload），
    # 自纠错 trail 属现场观察不入档（owner 裁决）。无 PATCH 端点（票 09 随
    # fresh_topic 闸撤除——话题连续性模型隐式判，会话面无运行时写入口）、
    # 无 DELETE 端点（票未划；删智能体连带清会话）。

    @app.get("/api/agents/{agent_id}/sessions")
    def list_sessions(agent_id: str) -> dict[str, Any]:
        try:
            store.get(agent_id)
            return {"sessions": sessions.list(agent_id)}
        except (AgentStoreError, SessionStoreError) as e:
            raise _bad(e) from None

    @app.get("/api/agents/{agent_id}/sessions/{sid}")
    def get_session(agent_id: str, sid: str) -> dict[str, Any]:
        try:
            store.get(agent_id)
            p = _pending.get(f"{agent_id}:{sid}")
            try:
                s = sessions.replay(agent_id, sid)
            except SessionNotFound:
                # M8 票 03 改判：懒建档无档但**有在途澄清**＝空档也放行（首问被反问后
                # 刷新，pending 是找回它的唯一路径）；无档无 pending 照旧 404 不放。
                if p is None:
                    raise
                s = Session(id=sid)
        except (AgentStoreError, SessionStoreError, FeedbackError) as e:
            raise _bad(e) from None
        # M8 票 04：旁挂票合并进回放——轮条目**可选尾键** feedback（chart 单点先例，
        # 无票轮形状不动、既有回放钉零红；同 ts 多票取末票）
        votes = latest_votes(store, agent_id, s.id)
        turns = []
        for t in s.turns:
            entry = {"question": t["question"], "failed": t["failed"],
                     "ts": t.get("ts"), "answer": t["answer"]}
            if t.get("ts") in votes:
                entry["feedback"] = votes[t["ts"]]
            turns.append(entry)
        return {"id": s.id, "turns": turns,
                # M8 票 03 改判：在途澄清（进程内指针）——刷新/回放后前端据此恢复
                # “待你补充”形态；None＝无待答（澄清轮本身照旧不落盘）
                "pending": None if p is None else
                {"question": p.question, "clarification": p.ask}}

    # ── M8 票 04：反馈面（旁挂票档，独立文件独立锁域）─────────────────
    # 裁决旁挂维持：不重开 PATCH、会话主档零改动——「会话面无运行时写入口」精神
    # 未被侵蚀（反馈不进 prompt/记忆/路由/AskResponse 契约）。写者不建会话：
    # sid 无会话档＝404；ts 回查不中＝票无处附，404。与 ask 在途锁零交叠
    # （不同文件不同锁，投票在回答中也可提交）。

    @app.post("/api/agents/{agent_id}/feedback")
    def post_feedback(agent_id: str, req: FeedbackRequest) -> dict[str, bool]:
        try:
            s = sessions.replay(agent_id, req.session_id)
        except (AgentStoreError, SessionStoreError) as e:
            raise _bad(e) from None
        turn = next((t for t in s.turns if t.get("ts") == req.ts), None)
        if turn is None:
            raise HTTPException(status_code=404,
                                detail=f"会话 {req.session_id} 无此轮（ts={req.ts}）")
        try:
            append_vote(store, agent_id, req.session_id, ts=req.ts, vote=req.vote,
                        question=turn["question"], sql=turn["answer"].get("sql"))
        except FeedbackError as e:
            raise _bad(e) from None
        return {"ok": True}

    # ── 问数：唯一入口，智能体定位数据源与默认业务知识 ───────────────
    # 在途锁（票 03→05）：/api/ask 与 /api/ask/stream 共用一把——防阻塞×流式交错。
    # 键＝session_id（票面"同会话并发"拒），单轮请求（无 session_id）维持 agent 级；
    # 前缀分域防 id 同值撞锁。单进程单用户约定（与上传闭包竞态同款，M7 spec）。
    _inflight: set[str] = set()
    _inflight_guard = threading.Lock()

    def _acquire_or_409(key: str, busy_msg: str) -> None:
        with _inflight_guard:
            if key in _inflight:
                raise HTTPException(status_code=409, detail=busy_msg)
            _inflight.add(key)

    def _release(key: str) -> None:
        with _inflight_guard:
            _inflight.discard(key)

    def _acquire_ask_lock(req: AskRequest, agent_name: str) -> str:
        """两端点共用锁的键与文案在此单收（防双份字面量漂移）：会话请求升格
        session_id 级（票 05 在途锁键升格），单轮请求维持 agent 级（票 03 契约）。
        返回键供收口释放。"""
        key = f"s:{req.session_id}" if req.session_id else f"a:{req.agent_id}"
        msg = ("该会话正在回答上一个问题，等进度流收口后再问" if req.session_id else
               f"智能体「{agent_name}」正在回答上一个问题，等进度流收口后再问")
        _acquire_or_409(key, msg)
        return key

    def _resolve_ask_target(req: AskRequest) -> tuple[str, str, str, Session | None]:
        """定位数据源＋装载会话记忆＋口径优先级（请求显式 > 智能体业务知识 > 空，
        票 02.5 语义——会话级叠加框经 owner 裁 2026-09-15 撤销）。全部前置校验，
        阻塞与流式两端点共用＝拒绝文案与顺序严格一致（流式端点不得自创一套）。"""
        try:
            meta = store.get(req.agent_id)
            path = store.datasource_path(meta)
            if path is None:
                raise HTTPException(status_code=400,
                                    detail=f"智能体「{meta.name}」未配置数据源（详情页上传 .sqlite 后再问）")
            session = sessions.load(req.agent_id, req.session_id) if req.session_id else None
            evidence = req.evidence.strip() or store.effective_evidence(meta)
        except (AgentStoreError, SessionStoreError) as e:
            raise _bad(e) from None
        return str(path), evidence, meta.name, session

    def _finish_ask(req: AskRequest, session: Session | None, answer: Answer,
                    question: str | None = None) -> dict[str, Any]:
        """两端点共同的收口：契约 payload（session_id 回显）＋会话轮次落盘。
        落盘＝L3 归档追加一轮；失败轮也入账（如实标失败，供下轮消解）。
        question＝入账题面，缺省取 req.question；HITL 续答轮显式传合成全句
        （compose_supplement 单源）＝题史在归档里自证。
        M8 票 03：**澄清轮不落盘**——落盘会造出 failed=False 且 sql=None 的第三种
        轮形态，牵动 _validated_turn/回放契约；暂停态住 checkpointer＋进程内指针，
        重开回放经 pending 字段恢复"待补充"形态（澄清史不再只活在标签页里）。
        写前重载合并原为 PATCH×ask 闸位竞态兜底（双轴评审收紧），票 09 随
        fresh_topic 闸与 PATCH 端点一并裁撤——闸撤后同会话写者只剩受在途锁
        排他的 ask 本身，装载快照即最新状态。"""
        payload = answer_to_payload(answer, session_id=req.session_id)
        if session is not None and not answer.clarification:
            merged = append_turn(session, question or req.question, res=answer.result,
                                 failed=answer.failed, payload=payload)
            sessions.save(req.agent_id, merged)
        return payload

    def _route_clarification(req: AskRequest, path: str, evidence: str, ctx,
                             on_event=None) -> tuple[Answer, str]:
        """经典 HITL 分流（M8 票 03 改判；两端点唯一闸口，在途锁内调用）：
        有待答澄清且未显式放弃＝本条消息按"补充"续跑原 thread（归档题面＝合成全句）；
        否则新问——**带会话且开了 checkpoint 才配 thread**（单轮/CLI/评测无 key 可续，
        照旧直 END 形态）。新问若以澄清收口＝落 pending 指针供下一条续跑。
        返回 (Answer, 归档题面)。"""
        pkey = f"{req.agent_id}:{req.session_id}" if req.session_id else None
        pend = _pending.pop(pkey, None) if pkey else None
        if req.discard_pending:
            pend = None
        if pend is not None:
            answer = resume_question(pend.thread, req.question.strip(), llm=llm,
                                     settings=settings, tracer=tracer, on_event=on_event,
                                     checkpointer=checkpoint)
            if answer.clarification:  # 不该发生（标记复闸保证续轮必答）；万一即塞回，不装没发生过
                _pending[pkey] = pend
            return answer, compose_supplement(pend.question, pend.ask, req.question)
        # thread 只在有会话＋有 checkpoint 时给；成对纪律由 run_question 闸口守死
        thread = f"{pkey}:{uuid.uuid4().hex[:8]}" if (pkey and checkpoint) else None
        answer = run_question(path, req.question, evidence=evidence, llm=llm,
                              settings=settings, tracer=tracer, on_event=on_event,
                              session_context=ctx, thread_id=thread, checkpointer=checkpoint)
        if thread is not None and answer.clarification:
            _pending[pkey] = _PendingAsk(thread=thread, question=req.question.strip(),
                                         ask=answer.clarification)
        return answer, req.question

    @app.post("/api/ask")
    def ask(req: AskRequest) -> dict[str, Any]:
        path, evidence, agent_name, session = _resolve_ask_target(req)
        key = _acquire_ask_lock(req, agent_name)
        try:
            answer, turn_q = _route_clarification(
                req, path, evidence, build_session_context(session) if session else None)
            return _finish_ask(req, session, answer, question=turn_q)
        finally:
            _release(key)

    # ── 票 03：进度流直播（SSE）——治"黑盒感"的主治通道 ──────────────

    @app.post("/api/ask/stream")
    def ask_stream(req: AskRequest) -> StreamingResponse:
        """前置拒绝与 /api/ask 同序同文案（400/404/409 走普通 JSON 错误，不起流）；
        起流后：进度帧＝data: {node,attempt,status(＋票 06 可选 ok/duration_ms/
        tokens_in/tokens_out／kind:tool 子事件)}\n\n，末帧＝event: answer＋
        契约本体（含票 04 chart 字段，与 /api/ask 同源 answer_to_payload，不另造；
        票 05 会话轮次落盘同样两端点同源——同走 _finish_ask，落盘在 runner 收口内）。
        run_question 仍是最外层
        守护——任何失败都以诚实失败答案收口成末帧，流永不裸断。
        桥接：worker 线程跑阻塞图（on_event＝入队），同步生成器逐帧取队 yield
        （starlette 自动 threadpool 迭代）。在途＝run_question 计算在途：放锁挂在
        runner 的 finally（评审收紧·双轴同指），断流/生成器未启动等一切投递路径
        都不构成泄漏窗口；计算跑完前队列缓冲、跑完即弃（单进程单用户尾差）。"""
        path, evidence, agent_name, session = _resolve_ask_target(req)
        key = _acquire_ask_lock(req, agent_name)
        events: queue.Queue = queue.Queue()
        box: dict[str, Any] = {}
        sentinel = object()
        ctx = build_session_context(session) if session else None

        def _runner() -> None:
            try:
                answer, turn_q = _route_clarification(req, path, evidence, ctx,
                                                      on_event=events.put)
                box["payload"] = _finish_ask(req, session, answer, question=turn_q)
            finally:
                events.put(sentinel)
                _release(key)  # 在途＝计算在途：投递侧任何路径不持锁

        threading.Thread(target=_runner, daemon=True).start()

        def _frames():
            while True:
                item = events.get()
                if item is sentinel:
                    break
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
            payload = box.get("payload") or answer_to_payload(  # worker 被 BaseException 掀翻也不裸断
                Answer(conclusion="流式查询异常中断", failed=True,
                       error_summary="流式查询异常中断"), session_id=req.session_id)
            yield ("event: answer\ndata: "
                   f"{json.dumps(payload, ensure_ascii=False)}\n\n")

        return StreamingResponse(_frames(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    if (dist / "index.html").is_file():
        # 构建产物同源服务（spec：零 CORS）；挂载放在 API 路由之后，不吃 /api/*
        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(dist / "index.html")

        if (dist / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
    else:

        @app.get("/", response_class=HTMLResponse)
        def placeholder() -> str:
            return _PLACEHOLDER_PAGE

    return app
