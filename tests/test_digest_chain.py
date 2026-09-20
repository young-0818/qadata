"""M9 票 05 专测：滚存摘要链＋懒补＋单层归并（spec §二 Q7／05 票面验收全表）。

钉五件事：
① 人造长会话三态测（纯函数）：成行→满批折段→段溢出丢最老，游标逐批推进正确、
  单次封顶（欠账无界防护）、折段与补行同一次调用（一次调用补多轮）；
② 懒补时机（端到端）：组装前同步补齐、无后台任务、补压调用经 timed_invoke 真名
  进审计账本（token 标记＝如实计一次 LLM 调用）＋digest outcome 行（无 token 标记
  ＝不烧调用数，budget_fuse 先例）；
③ 失败注入：调用挂/输出不合模板＝入账有痕、本轮照常作答、游标不动下次再补；
④ 旧档案无尾键＋短会话（无滑出）＝逐字节现状姊妹钉；CLI/eval 零触发（源码扫描）；
⑤ 淘汰序接线：超预算先丢窗口原文行、次摘要行、后纪要段（与票 04 序咬合、任务/输出不砍）。
零联网零 eval 花费（默认会话形态不滑出；懒补只在人为灌爆预算/长会话形态出现）。
"""
import inspect
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from qadata.graph import gssc
from qadata.graph.prompts import (
    DIGEST_LINE_HEADER,
    DIGEST_PARA_HEADER,
    digest_line_is_para,
    format_session_history,
    understand_prompt,
)
from qadata.llm.tracing import TraceLogger
from qadata.web import sessions as S
from qadata.web.agents import AgentStore
from qadata.web.app import create_app
from qadata.web.sessions import (
    Session,
    SessionStore,
    SessionStoreError,
    build_session_context,
    catch_up_digest,
)
from tests.fakes import BoomOnceLLM, ScriptedLLM
from tests.test_web_api import _HAPPY_SCRIPT, _S, _agent_with_datasource

_SID = "aabbccddeeff"
_Q0 = "2026 年有多少新生"


def _turn(i, failed=False):
    return {"question": f"问{i}", "ts": f"2026-09-20T10:00:{i:02d}+08:00",
            "failed": failed, "row_count": None if failed else 3,
            "head": "" if failed else "标量值 3",
            "answer": {"sql": None if failed else f"SELECT {i}"}}


def _sess(*ts, upto=0, digest=()):
    return Session(id=_SID, turns=tuple(ts), digest_lines=tuple(digest), digest_upto=upto)


def _rows(*ns):
    return [{"turn": n, "ts": "2026-09-20T11:00:00+08:00", "line": f"第{n}轮：摘要{n}"}
            for n in ns]


def _para(a, b):
    return {"turn": a, "ts": "2026-09-20T11:00:00+08:00", "line": f"第{a}-{b}轮：段落甲"}


# ── ① 三态测：成行→满批折段→段溢出丢最老，游标推进正确 ──────────────────


@pytest.fixture
def tiny_chain(monkeypatch, tmp_path):
    """灌爆窗＋缩批：预算 0＝滑出全部、批 3、段落存 2——三态在 4 次懒补内走满
    （默认 560/10/10 的换算钉在 test_default_constants 另守）。"""
    monkeypatch.setattr(S, "MEMORY_TOKEN_BUDGET", 0)
    monkeypatch.setattr(S, "DIGEST_BATCH", 3)
    monkeypatch.setattr(S, "DIGEST_PARA_CAP", 2)
    return TraceLogger(tmp_path / "traces.jsonl", run_id="r5")


def _lines_of(session):
    return [e["line"] for e in session.digest_lines]


def test_line_to_fold_to_para_overflow(tiny_chain):
    tracer = tiny_chain
    llm = ScriptedLLM([
        "第1轮：摘要一\n第2轮：摘要二\n第3轮：摘要三",                      # 成行
        "第4轮：摘要四\n第5轮：摘要五\n第6轮：摘要六\n第1-3轮：段落一",      # 满批折段（同一次调用）
        "第7轮：摘要七\n第8轮：摘要八\n第9轮：摘要九\n第4-6轮：段落二",
        "第10轮：摘要十\n第11轮：摘要十一\n第12轮：摘要十二\n第7-9轮：段落三",  # 段溢出丢最老
    ])
    s = _sess(*[_turn(i) for i in range(6)])
    s = catch_up_digest(s, llm=llm, tracer=tracer)
    assert s.digest_upto == 3
    assert _lines_of(s) == ["第1轮：摘要一", "第2轮：摘要二", "第3轮：摘要三"]
    assert llm.calls == 1  # 一次调用补多轮（≤DIGEST_BATCH 轮）

    s = catch_up_digest(_sess(*[_turn(i) for i in range(9)], upto=3, digest=s.digest_lines),
                        llm=llm, tracer=tracer)
    assert s.digest_upto == 6
    assert digest_line_is_para(s.digest_lines[0]["line"])  # 段落行＝冻存、永不复压
    assert _lines_of(s) == ["第1-3轮：段落一", "第4轮：摘要四", "第5轮：摘要五", "第6轮：摘要六"]
    # 折段料同调用给出；批数措辞随行数如实（不硬编码 10——单源钉）
    assert "第1-3轮：" in llm.prompts[1] and "- 第1轮：摘要一" in llm.prompts[1]
    assert "已满一批（共 3 条）" in llm.prompts[1]

    s2 = catch_up_digest(_sess(*[_turn(i) for i in range(12)], upto=6, digest=s.digest_lines),
                         llm=llm, tracer=tracer)
    assert s2.digest_upto == 9
    assert _lines_of(s2) == ["第1-3轮：段落一", "第4-6轮：段落二",
                             "第7轮：摘要七", "第8轮：摘要八", "第9轮：摘要九"]
    s3 = catch_up_digest(_sess(*[_turn(i) for i in range(15)], upto=9, digest=s2.digest_lines),
                         llm=llm, tracer=tracer)
    assert s3.digest_upto == 12
    assert _lines_of(s3)[:2] == ["第4-6轮：段落二", "第7-9轮：段落三"]  # 段落存 2，最老段入土
    assert s3.digest_lines[-1]["turn"] == 12
    assert llm.calls == 4 and tracer.usage_for("-")["llm_calls"] == 4  # 补压调用进账本（真账）


def test_batch_cap_and_undigested_tail(tiny_chain):
    """单次封顶 DIGEST_BATCH 轮——欠账再多也逐次推进（无界 prompt 防护），尾账下轮再补。"""
    llm = ScriptedLLM(["第1轮：a\n第2轮：b\n第3轮：c"])
    s = catch_up_digest(_sess(*[_turn(i) for i in range(9)]), llm=llm, tracer=tiny_chain)
    assert s.digest_upto == 3 and len(s.digest_lines) == 3  # 6 轮欠账只补最老 3
    assert s.digest_upto <= len(s.turns) - S._window_count(s.turns)  # 游标永不越过窗口边界


def test_failed_turn_material_says_so(monkeypatch):
    """待摘要素材如实标失败轮（错误素材不冒充结果——错误草稿不传染同款纪律）。"""
    monkeypatch.setattr(S, "MEMORY_TOKEN_BUDGET", 0)  # 逼出滑出（默认预算下小轮全在窗内）
    llm = ScriptedLLM(["第1轮：失败一轮"])
    s = catch_up_digest(_sess(_turn(0, failed=True)), llm=llm)
    assert "（该轮查询失败，无可靠结果）" in llm.prompts[0]
    assert s.digest_lines[0]["line"] == "第1轮：失败一轮"


# ── 严格回读（宁可不补不可补错）─────────────────────────────────────────


def test_parse_rejects_malformed_output():
    batch = [0, 1]
    assert S._parse_digest("第1轮：a", batch, []) is None  # 缺行
    assert S._parse_digest("第1轮：a\n第2轮：b\n第2轮：c", batch, []) is None  # 重号
    assert S._parse_digest("第1轮：a\n第2轮：b\n第9轮：x", batch, []) is None  # 多余行
    assert S._parse_digest("第1轮：\n第2轮：b", batch, []) is None  # 空正文
    assert S._parse_digest("好的，如下：\n第1轮：a\n第2轮：b", batch, []) is None  # 客套话
    ok = S._parse_digest("第1轮：a\n第2轮：b", batch, [])
    assert ok and ok["lines"] == {0: "第1轮：a", 1: "第2轮：b"} and ok["para"] is None
    # 折段回读：新行＋段落行各一（存全行），范围前缀错一位都不收
    pool = _rows(1, 2, 3)
    assert S._parse_digest("第4轮：d\n第1-3轮：段", [3], pool)["para"] == "第1-3轮：段"
    assert S._parse_digest("第4轮：d\n第1-4轮：段", [3], pool) is None


# ── 落盘：可选尾键 roundtrip／旧档现状／坏档如实炸 ─────────────────────


@pytest.fixture
def store(tmp_path):
    return AgentStore(tmp_path / "agents", metrics_dir=tmp_path / "metrics")


def test_digest_tail_roundtrip_and_guards(store):
    ss = SessionStore(store)
    a = store.create("摘要链智能体", "")
    sess = _sess(*[_turn(0), _turn(1)], upto=1, digest=_rows(1))
    ss.save(a.id, sess)
    assert ss.load(a.id, _SID) == sess
    text = (Path(store.agent_dir(a.id)) / "sessions" / f"{_SID}.yaml").read_text(encoding="utf-8")
    assert "digest_lines" in text and "digest_upto: 1" in text
    d = Path(store.agent_dir(a.id)) / "sessions"
    (d / "000000000000.yaml").write_text(
        "id: '000000000000'\nturns:\n- question: q\n  ts: '2026-09-20T10:00:00+08:00'\n"
        "  failed: false\n  row_count: 1\n  head: ''\n  answer: {}\n"
        "digest_lines: 不是列表\ndigest_upto: 1\n", encoding="utf-8")
    with pytest.raises(SessionStoreError):
        ss.load(a.id, "000000000000")
    (d / "111111111111.yaml").write_text(
        "id: '111111111111'\nturns:\n- question: q\n  ts: '2026-09-20T10:00:00+08:00'\n"
        "  failed: false\n  row_count: 1\n  head: ''\n  answer: {}\n"
        "digest_lines: [{turn: 1, ts: x, line: '第1轮：a'}]\ndigest_upto: 9\n", encoding="utf-8")
    with pytest.raises(SessionStoreError):  # 游标越界＝坏档，不装没看见
        ss.load(a.id, "111111111111")


# ── ④ 无滑出＝逐字节现状姊妹钉（旧档无尾键／短会话零触发）──────────────


def test_no_slide_is_identity_everywhere():
    """catch_up 原对象返回（零调用）、载荷不带 digest 键、渲染块与票 04 同串。"""
    sess = _sess(*[_turn(0), _turn(1)])
    llm = ScriptedLLM([])  # 越界即红：无滑出时模型一次都不该被碰
    assert catch_up_digest(sess, llm=llm) is sess and llm.calls == 0
    ctx = build_session_context(sess)
    assert "digest_lines" not in ctx
    assert format_session_history(ctx) == format_session_history(dict(ctx, digest_lines=[]))
    assert format_session_history(dict(ctx, digest_lines=None)) == format_session_history(ctx)


def test_legacy_file_without_tail_keys_loads_unchanged(store):
    a = store.create("旧档智能体", "")
    d = Path(store.agent_dir(a.id)) / "sessions"
    d.mkdir(parents=True)
    (d / f"{_SID}.yaml").write_text(
        "id: aabbccddeeff\nturns:\n- question: 旧问\n  ts: '2026-09-14T10:00:00+08:00'\n"
        "  failed: false\n  row_count: 1\n  head: 标量值 7\n  answer: {sql: 'SELECT 7'}\n",
        encoding="utf-8")
    s = SessionStore(store).load(a.id, _SID)
    assert s.digest_lines == () and s.digest_upto == 0
    ctx = build_session_context(s)
    assert "digest_lines" not in ctx  # 旧档装载形状＝入档前逐字节现状
    assert DIGEST_LINE_HEADER not in format_session_history(ctx)
    assert DIGEST_PARA_HEADER not in format_session_history(ctx)


def test_rendered_digest_block_order_and_authorization():
    """注入 [记忆] 形态＝段落行→行链→窗口原文（远→近、时间升序连续），节头带
    「无关则忽略」授权句（票 09 防线随新面延伸）。"""
    ctx = build_session_context(_sess(*[_turn(0), _turn(1)], upto=1,
                                 digest=[_para(1, 1)] + _rows(1)))
    block = format_session_history(ctx)
    assert block.index(DIGEST_PARA_HEADER) < block.index(DIGEST_LINE_HEADER) < block.index("## 会话历史")
    assert "第1-1轮：段落甲" in block and "第1轮：摘要1" in block.split(DIGEST_LINE_HEADER)[1]
    assert "忽略" in block.split("\n")[0] and "忽略" in block


def test_cli_and_eval_never_touch_digest():
    """零触发布线钉：CLI/eval 调用面源码出现 digest/catch_up 即红（懒补住 web 收口，
    单轮无会话无滑出＝结构上到不了）。"""
    import qadata.cli.main as cli_main
    import qadata.eval.bird as eval_bird

    for name in ("digest", "catch_up"):
        for mod in (cli_main, eval_bird):
            assert name not in inspect.getsource(mod), name


# ── ② 懒补时机（端到端）：组装前同步补齐、无后台任务、调用与入账 ─────────


def _client5(llm, store, tracer=None):
    return TestClient(create_app(llm=llm, settings=_S, agents=store, tracer=tracer,
                                 static_dir="__no_such_dist_for_tests__"))


def test_lazy_catchup_before_assembly_and_ledger(store, fixture_db, monkeypatch, tmp_path):
    """两问后第三问：滑出的第一问在 understand 之前被同一请求同步冻存——
    摘要 prompt＝第三问的第一发（无后台任务的结构证据＝响应返回时档案已带尾键）、
    账本＝digest 调用行（token 标记）＋outcome 行（不烧调用数）。"""
    monkeypatch.setattr(S, "MEMORY_TOKEN_BUDGET", 60)  # 现实小行 39 tok：窗 1 轮、二问即滑出
    a = _agent_with_datasource(store, fixture_db)
    llm = ScriptedLLM(_HAPPY_SCRIPT * 2 + ["第1轮：查得 2026 新生 120 人"] + _HAPPY_SCRIPT)
    tracer = TraceLogger(tmp_path / "traces.jsonl", run_id="r5")
    client = _client5(llm, store, tracer)
    base = {"agent_id": a.id, "question": _Q0, "session_id": _SID}
    client.post("/api/ask", json=base)
    client.post("/api/ask", json={**base, "question": "这些新生哪个班"})
    assert client.post("/api/ask",
                       json={**base, "question": "再按年级拆一下"}).json()["failed"] is False
    assert not any(p.startswith("你在为一段") for p in llm.prompts[:6])  # 前两问零触发（6 次常规调用）
    assert llm.prompts[6].startswith("你在为一段对话式数据分析会话整理滚存记忆")
    assert "第1轮：问题「" in llm.prompts[6]  # 素材＝滑出轮的问题与 SQL
    u3 = llm.prompts[7]
    assert "第1轮：查得 2026 新生 120 人" in u3 and "## 会话历史" in u3  # 行链＋窗口原文并注
    assert llm.calls == 10  # 3+3+（懒补 1＋常规 3）——懒补只在滑出时出现，一题一次封顶
    turn = SessionStore(store).load(a.id, _SID)  # 响应已返回＝补齐与落盘都同步完成（无后台）
    assert turn.digest_upto == 1 and turn.digest_lines[0]["line"] == "第1轮：查得 2026 新生 120 人"
    rows = [json.loads(ln) for ln in
            (tmp_path / "traces.jsonl").read_text(encoding="utf-8").splitlines()]
    calls = [r_ for r_ in rows if r_.get("node") == "digest" and "input_tokens" in r_]
    outcomes = [r_ for r_ in rows if r_.get("node") == "digest" and r_.get("outcome")]
    assert len(calls) == 1 and outcomes[0] == {"ts": outcomes[0]["ts"], "run_id": "r5",
                                               "node": "digest", "outcome": "ok",
                                               "digested": 1, "folded": 0}
    assert tracer.usage_for("-")["llm_calls"] == 10  # 补压＝真调用如实计；outcome 行无标记不烧数


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_stream_lock_survives_digest_save_crash(store, fixture_db, monkeypatch):
    """双轴评审追补（Spec (c)1）：流式端点的懒补落盘炸（裸 OSError）＝诚实末帧＋
    会话锁照常释放——旧挂点（锁在 acquire 与 runner 之间抛）会永久悬锁＝该会话
    直到重启前逢问 409。worker 线程如实带异常收口（挂点在 _runner 的 try/finally 内），
    线程异常告警即本测题中之义，滤之。"""
    monkeypatch.setattr(S, "MEMORY_TOKEN_BUDGET", 60)
    a = _agent_with_datasource(store, fixture_db)
    llm = ScriptedLLM(_HAPPY_SCRIPT * 2 + ["第1轮：甲"] + ["第1轮：甲"] + _HAPPY_SCRIPT)
    client = _client5(llm, store)
    base = {"agent_id": a.id, "question": _Q0, "session_id": _SID}
    client.post("/api/ask", json=base)
    client.post("/api/ask", json={**base, "question": "这些新生哪个班"})  # 此后第三问必有滑出
    real_save = SessionStore.save
    calls = {"n": 0}

    def flaky_save(self, agent_id, session):
        if session.digest_lines:  # 只炸懒补那一次落盘（带摘要、轮未追加）
            calls["n"] += 1
            raise OSError("磁盘罢工")
        return real_save(self, agent_id, session)

    monkeypatch.setattr(SessionStore, "save", flaky_save)
    r = client.post("/api/ask/stream", json={**base, "question": "再按年级拆一下"})
    assert r.status_code == 200 and '"failed": true' in r.text  # 诚实末帧，流不裸断
    monkeypatch.setattr(SessionStore, "save", real_save)
    assert calls["n"] == 1
    r = client.post("/api/ask/stream", json={**base, "question": "换个视角"})  # 锁未被悬住＝不 409
    assert r.status_code == 200 and '"failed": false' in r.text
    assert SessionStore(store).load(a.id, _SID).digest_upto == 1  # 下次再补照旧兑现


def test_short_sessions_never_invoke_digest(store, fixture_db):
    """默认预算下现实小会话＝零触发：三问全程 9 次调用、零 digest 面、档案无尾键。"""
    a = _agent_with_datasource(store, fixture_db)
    llm = ScriptedLLM(_HAPPY_SCRIPT * 3)
    client = _client5(llm, store)
    base = {"agent_id": a.id, "question": _Q0, "session_id": _SID}
    for q in (_Q0, "这些新生哪个班", "按年级拆"):
        client.post("/api/ask", json={**base, "question": q})
    assert llm.calls == 9 and all(not p.startswith("你在为一段") for p in llm.prompts)
    assert not any("digest" in p for p in llm.prompts)
    assert SessionStore(store).load(a.id, _SID).digest_lines == ()


# ── ③ 失败注入：不拦答题、下次再补、入账有痕 ──────────────────────────


def test_digest_garbage_keeps_answer_and_retries_next(store, fixture_db, monkeypatch, tmp_path):
    """垃圾输出（不合模板）＝整次作废：游标不动、outcome=failed 入账、本轮照常作答；
    下一问再补（素材还在，逐批补齐）。"""
    monkeypatch.setattr(S, "MEMORY_TOKEN_BUDGET", 60)
    a = _agent_with_datasource(store, fixture_db)
    llm = ScriptedLLM(_HAPPY_SCRIPT * 2 + ["好的，但我不守模板"] + _HAPPY_SCRIPT
                      + ["第1轮：甲\n第2轮：乙"] + _HAPPY_SCRIPT)
    tracer = TraceLogger(tmp_path / "traces.jsonl", run_id="r5")
    client = _client5(llm, store, tracer)
    base = {"agent_id": a.id, "question": _Q0, "session_id": _SID}
    for q in (_Q0, "这些新生哪个班", "再拆一下", "换个视角"):
        assert client.post("/api/ask", json={**base, "question": q}).json()["failed"] is False
    turn = SessionStore(store).load(a.id, _SID)
    assert turn.digest_upto == 2 and _lines_of(turn) == ["第1轮：甲", "第2轮：乙"]
    rows = [json.loads(ln) for ln in
            (tmp_path / "traces.jsonl").read_text(encoding="utf-8").splitlines()]
    bad = [r_ for r_ in rows if r_.get("node") == "digest" and r_.get("outcome") == "failed"]
    good = [r_ for r_ in rows if r_.get("node") == "digest" and r_.get("outcome") == "ok"]
    assert len(bad) == 1 and "不合模板" in bad[0]["reason"] and len(good) == 1
    assert llm.calls == 14  # 3+3+(1+3)+(1+3)——失败的那次调用照计（真花了 token 的真账）


def test_digest_call_crash_logged_and_deferred(store, fixture_db, monkeypatch, tmp_path):
    """调用抛异常＝同纪律：不拦答题、游标不动、failed 入账（timed_invoke 未收口＝
    不冒充真账、不烧调用数）。"""
    monkeypatch.setattr(S, "MEMORY_TOKEN_BUDGET", 60)
    a = _agent_with_datasource(store, fixture_db)
    llm = BoomOnceLLM(_HAPPY_SCRIPT * 3, boom_marker="滚存记忆")
    tracer = TraceLogger(tmp_path / "traces.jsonl", run_id="r5")
    client = _client5(llm, store, tracer)
    base = {"agent_id": a.id, "question": _Q0, "session_id": _SID}
    for q in (_Q0, "这些新生哪个班", "再按年级拆一下"):
        assert client.post("/api/ask", json={**base, "question": q}).json()["failed"] is False
    turn = SessionStore(store).load(a.id, _SID)
    assert turn.digest_lines == () and turn.digest_upto == 0
    rows = [json.loads(ln) for ln in
            (tmp_path / "traces.jsonl").read_text(encoding="utf-8").splitlines()]
    bad = [r_ for r_ in rows if r_.get("node") == "digest"]
    assert len(bad) == 1 and bad[0]["outcome"] == "failed" and "调用失败" in bad[0]["reason"]
    assert "input_tokens" not in bad[0]
    assert tracer.usage_for("-")["llm_calls"] == 9  # 三问常规链 9 次，炸掉的摘要不计数


# ── ⑤ 淘汰序接线：超预算先丢（窗口原文）行、次摘要行、后纪要段 ──────────


def test_fuse_evicts_window_then_lines_then_paragraphs(monkeypatch):
    """与票 04 序咬合：窗口原文行最先（最啰嗦）、摘要行次之、纪要段最后（密度越高
    越守得住）；任务与输出面永不砍。"""
    ctx = build_session_context(_sess(*[_turn(i) for i in range(3)], upto=3,
                                     digest=[_para(1, 2)] + _rows(3)))
    slots = gssc.gather_understand({"question": "这些呢", "evidence": "口径",
                                    "session_context": ctx}, clarify=False)
    monkeypatch.setitem(gssc.FUSE_TOKENS, "understand", 0)  # 全灌爆：三级走满
    events = []
    out = gssc.assemble("understand", slots, on_compress=events.append)
    acts = events[0]["actions"]
    assert acts.count("丢最老记忆行") == 3 and acts.count("丢最老摘要行") >= 1
    assert "丢最老纪要段" in acts
    i_window_last = max(i for i, a in enumerate(acts) if a == "丢最老记忆行")
    i_line_first = min(i for i, a in enumerate(acts) if a == "丢最老摘要行")
    i_line_last = max(i for i, a in enumerate(acts) if a == "丢最老摘要行")
    i_para_first = min(i for i, a in enumerate(acts) if a == "丢最老纪要段")
    assert i_window_last < i_line_first <= i_line_last < i_para_first, acts
    assert "第1-2轮" not in out and "第3轮" not in out and "- 问：" not in out
    assert "原始问题：这些呢" in out and "只输出一个 JSON 对象" in out  # 任务/输出永存


# ── 默认常量钉（K=5 换算与封顶形态在册）───────────────────────────────


def test_default_constants_are_the_pinned_conversion():
    assert S.MEMORY_TOKEN_BUDGET == 560  # 现实最坏行 104×5＝520 容、×6 出＝K=5 默认换算
    assert S.DIGEST_BATCH == 10 and S.DIGEST_PARA_CAP == 10  # 注入最坏 ≈10 段＋9 行，有界
    sess = _sess(*[_turn(i) for i in range(10)])
    assert S._window_count(sess.turns) == 10  # 小行全在预算窗内＝短会话零滑出零触发（默认形态）


def test_closed_state_prompt_never_mentions_digest():
    """无会话＝关态逐字节现状：oracle 路 understand prompt 零染指摘要面。"""
    assert DIGEST_LINE_HEADER not in understand_prompt("有几名学生", "口径", "")
