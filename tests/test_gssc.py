"""M9 票 03 GSSC 收编金标准测：四场景「装配器出口 vs 旧路（oracle）」双跑 diff 零差异
（M5 metric_match 场景已随指标层退役，ADR-0007——原五场景）。

票面验收＝纯结构收编、prompt 文本逐字节原样——每场景多输入变体（含花括号/节头字样/
标记位/截断等坏输入）新旧两路逐字节对拍；另钉分区词汇（六分区落位＋各区序列）、
Select/Compress 恒等形状（票 04/06 的入住插座在位）与收编布线（nodes/schema 直呼旧
装配函数即红——防静默退回散装配）。零联网零 LLM 调用（纯函数层）＋一条端到端布线对拍。
"""
import inspect

from qadata.config import Settings
from qadata.graph import gssc
from qadata.graph.build import run_question
from qadata.graph.gssc import Zone
from qadata.graph.nodes import format_rows
from qadata.graph.prompts import (
    _PICK_PROMPT,
    SUPPLEMENT_MARK,
    TRUNCATION_HINT,
    format_failure_history,
    format_session_draft,
    format_session_history,
    respond_prompt,
    sql_prompt,
    understand_prompt,
)
from qadata.types import QueryResult, SqlAttempt
from tests.fakes import ScriptedLLM

_S = Settings(api_key="", base_url="", model="test-model", retry_budget=3)
_GOOD = "SELECT name FROM students WHERE id = 2"

_CTX = {
    "turns": [{"question": "2026 年有多少新生", "sql": "SELECT COUNT(*) FROM student",
               "row_count": 1, "head": "标量值 120", "failed": False}],
    "draft": {"sql": "SELECT COUNT(*) FROM student", "head": "标量值 120"},
}


# ── 四场景双跑金标准（装配器出口 vs 旧路）─────────────────────────────


def test_understand_byte_equal_oracle():
    for q, ev, ctx, clarify in [
            ("谁成绩最好", "", None, False),
            ("谁成绩最好", "全名 = first+last", None, True),
            ("这些学生呢", "口径", _CTX, False),
            (f"问{SUPPLEMENT_MARK}澄清？ 答", "口径", None, True),  # 防循环闸：带标记不加尾段
            ("带 {花括号}{{转义}} 与 ## 用户问题\n 的题面", "{x} 与 {{y}}\n", _CTX, True)]:
        # （ev＝退役键：state 残键也不得入 prompt——防回吹钉；口径唯一通道＝字典块进 generate）
        state = {"question": q, "evidence": ev, "session_context": ctx}
        out = gssc.assemble("understand", gssc.gather_understand(state, clarify=clarify))
        assert out == understand_prompt(q, format_session_history(ctx), clarify=clarify)
        assert not ev.strip() or ("evidence" not in out and ev not in out)


def test_generate_byte_equal_oracle():
    _bad = SqlAttempt(sql="SELECT bad", error="no such table: bad")
    for attempts, vn, ctx in [
            ([], None, None),
            ([_bad, SqlAttempt(sql="", error="提取失败")], "结果为空", None),
            ([], None, _CTX),
            ([_bad], None, _CTX)]:
        for schema, ev, q in [("SCHEMA", "", "有几名学生"),
                              ("带 ## 背景信息\n与 {braces}{{x}} 的 schema",
                               "{e}{{v}}", "题面 {q} 也带花括号")]:
            state = {"question": q, "evidence": ev, "db_schema": schema,
                     "attempts": attempts, "verify_note": vn,
                     "session_context": ctx}
            out = gssc.assemble("generate", gssc.gather_generate(state))
            assert out == sql_prompt(
                schema=schema, question=q,
                history=format_failure_history(attempts, vn),
                draft=format_session_draft(ctx))
            assert not ev.strip() or ("evidence" not in out and ev not in out)  # 防回吹同钉


def test_respond_byte_equal_oracle():
    full = QueryResult(columns=["a"], rows=[(1,), (2,)], row_count=2,
                       truncated=False, elapsed_ms=1)
    trunc = QueryResult(columns=["a"], rows=[(1,)], row_count=9999,
                        truncated=True, elapsed_ms=1)
    for res, sql in [(full, "SELECT a FROM t"), (trunc, "SELECT a FROM t"),
                     (full, None), (full, "")]:
        state = {"question": "有几名学生", "evidence": "口径"}
        rows = format_rows(res)
        assert gssc.assemble("respond", gssc.gather_respond(
            state, result=res, sql=sql, rows_table=rows, n=len(res.rows))) == respond_prompt(
            question="有几名学生", sql=sql or "",
            rows_table=rows + (TRUNCATION_HINT if res.truncated else ""),
            total=res.row_count, n=len(res.rows))


def test_explore_byte_equal_oracle():
    for tables, q in [(["student", "loan", "account"], "谁成绩最好"),
                      (["只有表"], "带,逗号 与 {braces} 的题面")]:
        assert gssc.assemble("explore", gssc.gather_schema_pick(
            tables, q)) == _PICK_PROMPT.format(tables=", ".join(tables), question=q)


# ── 分区词汇落地（ADR-0002：内部分类法、非字节顺序）───────────────────

_LIVE_SLOTS = {
    "understand": lambda: gssc.gather_understand(
        {"question": "Q", "evidence": "E", "session_context": _CTX}, clarify=True),
    "generate": lambda: gssc.gather_generate(
        {"question": "Q", "evidence": "", "db_schema": "S", "attempts": [],
         "session_context": _CTX}),
    "respond": lambda: gssc.gather_respond(
        {"question": "Q"}, result=QueryResult(columns=["a"], rows=[(1,)], row_count=1,
                                              truncated=False, elapsed_ms=1),
        sql="SELECT 1", rows_table="a\n1", n=1),
    "explore": lambda: gssc.gather_schema_pick(["t1", "t2"], "Q"),
}

_EXPECTED_ZONES = {
    "understand": (Zone.TASK, Zone.MEMORY, Zone.TASK, Zone.OUTPUT),
    "generate": (Zone.ROLE, Zone.EVIDENCE, Zone.TASK,
                 Zone.STATE, Zone.MEMORY, Zone.OUTPUT),
    "respond": (Zone.ROLE, Zone.TASK, Zone.STATE, Zone.EVIDENCE, Zone.OUTPUT),
    "explore": (Zone.EVIDENCE, Zone.TASK, Zone.OUTPUT),
}


def test_six_zone_vocabulary():
    assert {z.value for z in Zone} == {"角色与政策", "任务", "状态", "证据", "记忆", "输出"}


def test_partition_landing_per_scenario():
    for scenario, expected in _EXPECTED_ZONES.items():
        sections = gssc.structure(scenario, _LIVE_SLOTS[scenario]())
        assert tuple(s.zone for s in sections) == expected, scenario


def test_select_and_compress_are_identity_stages():
    """未挂接＝恒等不改一分：Select 缺省 recall＝原样返回（票 06 上岗后默认关形态、
    空池逐字节现状的插座面），Compress 限内＝仅拼接（分区序列不重排、空段不吞脚手架）。
    挂接态与降级态的钉在 tests/test_embedding_recall.py。"""
    slots = _LIVE_SLOTS["generate"]()
    assert gssc.select("generate", slots) is slots
    sections = gssc.structure("generate", slots)
    assert gssc.compress("generate", sections) == "".join(s.text for s in sections)
    assert gssc.assemble("generate", slots) == gssc.compress("generate", sections)


def test_unknown_scenario_refused():
    try:
        gssc.structure("chat", {})
    except KeyError as e:
        assert "chat" in str(e)
    else:
        raise AssertionError("未登记场景须拒——收编出口只认四场景")


# ── 收编布线（节点侧不再伸手拿料、不得静默退回旧装配函数）─────────────


def test_nodes_route_through_assembler():
    import qadata.graph.nodes as nodes_mod
    import qadata.tools.schema as schema_mod

    src, ssrc = inspect.getsource(nodes_mod), inspect.getsource(schema_mod)
    for old in ("understand_prompt(", "sql_prompt(", "respond_prompt(",
                "format_failure_history(",
                "format_session_history(", "format_session_draft("):
        assert old not in src, f"nodes.py 直呼旧装配/拿料函数＝退回散装配：{old}"
    for name in ("understand", "generate", "respond"):
        assert f'assemble("{name}"' in src, name
    assert "_PICK_PROMPT" not in ssrc and 'assemble("explore"' in ssrc


def test_node_prompts_match_oracle_end_to_end(fixture_db):
    """布线端到端对拍（假模型零联网）：真链路产出的 understand/respond prompt 与旧路
    oracle 逐字节一致；generate 的输入件收口在 state、由上组单测钉输入等价，此处钉调用数。"""
    llm = ScriptedLLM(["改写句", _GOOD, "结论"])
    ans = run_question(fixture_db, "有几名学生", llm=llm, settings=_S)
    assert ans.failed is False and llm.calls == 3  # 零新增调用（收编不收租）
    assert llm.prompts[0] == understand_prompt("有几名学生", "")
    r = ans.result
    assert llm.prompts[2] == respond_prompt("改写句", ans.sql or "", format_rows(r),
                                            r.row_count, len(r.rows))
