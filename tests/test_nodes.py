import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from qadata.graph.nodes import extract_sql, format_rows, make_nodes
from qadata.types import QueryResult
from tests.conftest import RecorderLLM
from tests.fakes import ScriptedLLM


def test_extract_sql_strips_fences():
    assert extract_sql("```sql\nSELECT 1;\n```") == "SELECT 1"
    assert extract_sql("SELECT 2") == "SELECT 2"
    with pytest.raises(ValueError, match="SQL"):
        extract_sql("抱歉，我不会")


def test_format_rows_pipe_table():
    r = QueryResult(columns=["name", "score"], rows=[("A", 1.0)], row_count=1, truncated=False, elapsed_ms=1)
    t = format_rows(r)
    assert "name" in t and "A" in t and "1.0" in t


def test_nodes_pass_limiter_to_timed_invoke(monkeypatch):
    """限速器透传：make_nodes 注入的 limiter 必须进全部 timed_invoke 调用。"""
    captured = {}

    def fake_timed_invoke(llm, prompt, node, tracer, limiter=None, sink=None):
        captured[node] = limiter
        captured["sink_" + node] = sink
        return "ok"

    monkeypatch.setattr("qadata.graph.nodes.timed_invoke", fake_timed_invoke)
    sentinel = object()
    nodes = make_nodes(None, None, limiter=sentinel)
    nodes["understand"]({"question": "q"})
    assert captured["understand"] is sentinel
    # M8 票 06：on_event 缺省 None＝sink 也为 None（关态零 token 累计，逐行为一致）
    assert captured["sink_understand"] is None


def test_skip_respond_placeholder_conclusion_without_llm(fixture_db):
    """评测模式 --skip-respond：成功路径不调 LLM（ScriptedLLM 空脚本零容忍），
    占位结论入 Answer，sql/result 原样保留（判分只读 sql）。"""
    from tests.fakes import ScriptedLLM

    llm = ScriptedLLM(["每个学生的平均成绩是多少", "SELECT name FROM students WHERE id = 1"])
    nodes = make_nodes(llm, skip_respond=True)
    state = {"db_path": fixture_db, "question": "谁成绩最好"}
    state.update(nodes["understand"](state))
    state.update(nodes["explore"](state))
    state.update(nodes["generate"](state))
    state.update(nodes["execute"](state))
    state.update(nodes["verify"](state))
    state.update(nodes["respond"](state))
    ans = state["answer"]
    assert llm.calls == 2  # understand + generate；respond 未调（超脚本即炸）
    assert ans.failed is False
    assert ans.sql == "SELECT name FROM students WHERE id = 1"
    assert ans.result.rows == [("Alice",)]
    assert "跳过结论生成" in ans.conclusion


def test_skip_respond_failure_path_stays_honest(fixture_db):
    """失败路径（执行失败耗尽）仍走确定性诚实汇报，skip 只作用于成功路径。"""
    from tests.fakes import ScriptedLLM

    llm = ScriptedLLM(["q", "SELECT nope FROM students"])
    nodes = make_nodes(llm, skip_respond=True)
    state = {"db_path": fixture_db, "question": "谁成绩最好"}
    state.update(nodes["understand"](state))
    state.update(nodes["explore"](state))
    state.update(nodes["generate"](state))
    state.update(nodes["execute"](state))
    state.update(nodes["verify"](state))
    state.update(nodes["respond"](state))
    ans = state["answer"]
    assert llm.calls == 2  # 失败汇报本就不调 LLM（永不编造）
    assert ans.failed is True
    assert "未能完成查询" in ans.conclusion and "nope" in ans.conclusion


def test_nodes_happy_path(fixture_db):
    llm = FakeListChatModel(
        responses=["每个学生的平均成绩是多少", "SELECT name FROM students WHERE id = 1", "Alice 的成绩最好"]
    )
    nodes = make_nodes(llm)
    state = {"db_path": fixture_db, "question": "谁成绩最好"}
    state.update(nodes["understand"](state))
    assert state["question"] == "每个学生的平均成绩是多少"
    state.update(nodes["explore"](state))
    assert "CREATE TABLE" in state["db_schema"]
    state.update(nodes["generate"](state))
    assert state["current_sql"] == "SELECT name FROM students WHERE id = 1"
    state.update(nodes["execute"](state))
    assert state["result"].rows == [("Alice",)]
    state.update(nodes["respond"](state))
    ans = state["answer"]
    assert ans.failed is False
    # 票 07：三节组装——LLM 文本进【结论】节，数据依据确定性派生
    assert ans.conclusion.startswith("【结论】Alice 的成绩最好")
    assert "所用表：students" in ans.conclusion
    assert ans.sql == "SELECT name FROM students WHERE id = 1"


def test_nodes_no_sql_fails_honestly(fixture_db):
    llm = FakeListChatModel(responses=["改写后的问题", "对不起，我回答不了"])
    nodes = make_nodes(llm)
    state = {"db_path": fixture_db, "question": "谁成绩最好"}
    state.update(nodes["understand"](state))
    state.update(nodes["explore"](state))
    state.update(nodes["generate"](state))  # 提取不到 SQL
    assert state.get("current_sql") is None and state.get("last_error")
    state.update(nodes["execute"](state))  # 无 SQL → no-op
    state.update(nodes["respond"](state))
    ans = state["answer"]
    assert ans.failed is True and ans.sql is None
    assert "未能" in ans.conclusion  # 永不编造


def test_nodes_sql_error_recorded(fixture_db):
    llm = FakeListChatModel(responses=["q", "SELECT nope FROM students", "r"])
    nodes = make_nodes(llm)
    state = {"db_path": fixture_db, "question": "q"}
    for name in ("understand", "explore", "generate"):
        state.update(nodes[name](state))
    state.update(nodes["execute"](state))
    assert state["result"] is None and "no such column" in state["last_error"]
    assert state["attempts"] and state["attempts"][-1].error  # 失败历史
    state.update(nodes["respond"](state))
    assert state["answer"].failed is True


def test_understand_keeps_original_question():
    from qadata.graph.nodes import make_nodes
    from tests.fakes import ScriptedLLM

    nodes = make_nodes(ScriptedLLM(["改写后的问题"]))
    out = nodes["understand"]({"question": "原始问题"})
    # M5 票 02 起 understand 多返回 intent 键（纯文本＝解析失败回退态 None，同 test_intent.py）
    assert out == {"original_question": "原始问题", "question": "改写后的问题", "intent": None}


def test_generate_failure_records_attempt(fixture_db):
    """§11.1：generate 提取失败也写 SqlAttempt——len(attempts) 才是完整的预算账本。"""
    from qadata.graph.nodes import make_nodes
    from tests.fakes import ScriptedLLM

    nodes = make_nodes(ScriptedLLM(["对不起，我不会写 SQL"]))
    state = {"db_path": fixture_db, "question": "q", "db_schema": "CREATE TABLE students (id INTEGER);"}
    out = nodes["generate"](state)
    assert out["current_sql"] is None
    assert len(out["attempts"]) == 1 and out["attempts"][0].sql == ""
    assert "未找到合法 SQL" in out["attempts"][0].error


def test_respond_notes_truncation():
    """截断提示：truncated=True 时 respond prompt 必须带截断说明，且预览行数为 PREVIEW_ROWS。"""
    res = QueryResult(
        columns=["name"],
        rows=[(f"r{i}",) for i in range(12)],
        row_count=12,
        truncated=True,
        elapsed_ms=1,
    )
    recorder = RecorderLLM(["结论"])
    nodes = make_nodes(recorder)
    state = {
        "db_path": "unused",
        "question": "q",
        "current_sql": "SELECT name FROM students",
        "result": res,
        "last_error": None,
    }
    out = nodes["respond"](state)
    assert out["answer"].failed is False
    prompt = recorder.prompts[0]
    assert "结果已截断" in prompt  # 提示模型如实措辞，勿把截断当全量
    assert "前 10 行" in prompt  # n 由 PREVIEW_ROWS 派生


# ── M5 票 07：E1 三节组装（respond 节点）───────────────────────────


def _ok_result(rows=(("Alice",),), row_count=None, truncated=False):
    return QueryResult(columns=["name"], rows=list(rows),
                       row_count=row_count if row_count is not None else len(rows),
                       truncated=truncated, elapsed_ms=1)


def _respond_state(**over):
    state = {"db_path": "unused", "question": "q",
             "current_sql": "SELECT name FROM students WHERE id = 1",
             "result": _ok_result(), "last_error": None}
    state.update(over)
    return state


def test_respond_success_minimal_three_sections_without_idle():
    """兜底题无口径来源、无标注：三节中该省的省——口径/校验两节整体不出现（不空转）。"""
    llm = ScriptedLLM(["Alice 最好"])
    out = make_nodes(llm)["respond"](_respond_state())
    assert llm.calls == 1  # 纪律⑤：三节组装不偷偷多烧调用
    c = out["answer"].conclusion
    assert c.startswith("【结论】Alice 最好")
    assert "【数据依据】共取到 1 行（全部列示）；所用表：students" in c
    assert "【口径说明】" not in c and "【校验标注】" not in c


def test_respond_caliber_reads_knowledge_block_zero_extra_call():
    """口径说明节＝本题字典召回块首行（ADR-0008 字典单通道；memo 共担零新调用零 token）。"""
    from qadata.retrieval.knowledge import format_knowledge_block
    kb = format_knowledge_block(["正常贷款：状态 'A' 计为正常。",
                                 "多行条目首行\n  缩进续行"])
    llm = ScriptedLLM(["Alice 最好"])
    out = make_nodes(llm, knowledge_recall=lambda q: kb)["respond"](_respond_state())
    assert llm.calls == 1  # 展示零 token：respond 仍只有一次调用
    c = out["answer"].conclusion
    assert "【口径说明】" in c and "正常贷款：状态 'A' 计为正常。" in c
    assert "缩进续行" not in c  # 展示只取各条首行（全文已随 generate 上下文，不重灌）
    assert "【校验标注】" not in c


def test_respond_caliber_omitted_when_kb_absent():
    """未挂字典/召回空/降级空串 → 口径节省略（现状逐字节，空节省略旧钉语义搬家）。"""
    for recall in (None, lambda q: ""):
        llm = ScriptedLLM(["r"])
        out = make_nodes(llm, knowledge_recall=recall)["respond"](_respond_state())
        assert llm.calls == 1
        assert "【口径说明】" not in out["answer"].conclusion


def test_respond_truncation_and_verify_notes_merged_into_check_section():
    """截断标注与校验可疑并入【校验标注】节，逐条列、不另开新节（票面条款④）。"""
    res = _ok_result(rows=[(f"r{i}",) for i in range(12)], row_count=12, truncated=True)
    llm = ScriptedLLM(["见明细"])
    state = _respond_state(result=res, verify_note="结果为空")
    out = make_nodes(llm)["respond"](state)
    assert llm.calls == 1
    c = out["answer"].conclusion
    assert "【校验标注】" in c
    assert "- 该结果未通过自动校验：结果为空" in c
    assert "- 结果已截断：结论仅基于前 10 行预览生成" in c  # 不重复行数，只陈述预览覆盖事实
    assert c.index("- 该结果未通过自动校验") < c.index("- 结果已截断")


def test_respond_failure_path_honest_sections_and_zero_llm():
    """失败态：respond 不调 LLM（ScriptedLLM 空脚本＝超脚本即炸），仍出三节诚实形态。"""
    from qadata.types import SqlAttempt

    llm = ScriptedLLM([])
    state = {"db_path": "unused", "question": "q", "current_sql": None, "result": None,
             "attempts": [SqlAttempt(sql="SELECT nope FROM students",
                                     error="no such column: nope")],
             "last_error": "no such column: nope"}
    out = make_nodes(llm)["respond"](state)
    ans = out["answer"]
    assert llm.calls == 0 and ans.failed is True  # 失败态零 LLM 显式入账
    c = ans.conclusion
    assert "【结论】未能完成查询（共尝试 1 次）" in c
    assert "【数据依据】无成功执行的查询，无可用结果集" in c


# （M5 模板降级标注测已随指标层退役删除，ADR-0007。）

# ── M8 票 07：答案报告化（markdown 结论原样穿三节组装）─────────────

_REPORT = """## 总览
本月贷款共 3 笔。

- 违约：1 笔
- 正常：2 笔

| 状态 | 笔数 |
| --- | --- |
| B | 1 |
| C | 2 |"""


def test_respond_report_markdown_passes_through_untouched():
    """版式测：多段 markdown 结论逐字进【结论】节（换行不折叠、表格不重排），
    代码组装节照旧跟在报告之后。渲染元素实景归前端 Markdown.tsx＋真链路眼验
    （壳不判卷、CI 纯 Python）——本钉的是后端零改动透传：报告化不碰组装。"""
    llm = ScriptedLLM([_REPORT])
    out = make_nodes(llm)["respond"](_respond_state())
    assert llm.calls == 1  # 报告化零新增调用（纪律 #5）
    c = out["answer"].conclusion
    assert c.startswith("【结论】## 总览\n")  # 节头与报告首行同段共存
    assert "\n| B | 1 |\n" in c              # 表格行原样，不转义不吞竖线
    assert c.index("【数据依据】") > c.index("| C | 2 |")  # 代码节排在报告后


def test_respond_report_section_headers_never_split():
    """报告正文若含行首【…】（模型违令模仿节头），后端组装不为此改形——
    分节白名单是前端唯一识别面（App.tsx SECTION_RE 白名单钉见 test_web_markdown）。"""
    llm = ScriptedLLM(["## 小结\n【已完成】放款 3 笔。\n【未完成】催收 1 笔。"])
    c = make_nodes(llm)["respond"](_respond_state())["answer"].conclusion
    assert llm.calls == 1
    assert "【已完成】放款 3 笔。" in c  # 载荷层不吞不改（前端白名单不当它节头）
    assert c.count("【数据依据】") == 1  # 真节头唯一，由 compose_conclusion 单源产出


def test_respond_long_report_not_truncated():
    """超长策略如实记：**不截断**——报告长度被预览行输入天然有界，中间截断会切坏
    markdown 形态（表格/围栏半具尸）；权威数字在结果表，与报告漂移可对照。本钉＝
    超长结论原样过节点，无任何静默砍尾。"""
    long_report = "## 长报告\n" + "\n".join(f"- 第 {i} 条发现：数值 {i}" for i in range(500))
    llm = ScriptedLLM([long_report])
    ans = make_nodes(llm)["respond"](_respond_state())["answer"]
    assert llm.calls == 1
    assert ans.conclusion.endswith("【数据依据】共取到 1 行（全部列示）；所用表：students")
    assert "- 第 499 条发现：数值 499" in ans.conclusion  # 末行在场＝没被砍
    assert len(ans.conclusion) > len(long_report)


def test_last_good_sql_derived_from_attempts():
    from qadata.graph.nodes import _last_good_sql
    from qadata.types import SqlAttempt

    attempts = [
        SqlAttempt(sql="", error="回复中未找到合法 SQL"),   # 提取失败：跳过
        SqlAttempt(sql="SELECT a", row_count=1),            # 成功
        SqlAttempt(sql="SELECT b", error="no such column"),  # 执行失败：跳过
        SqlAttempt(sql="", error="仍未提取到"),              # 提取失败：跳过
    ]
    assert _last_good_sql(attempts) == "SELECT a"
    assert _last_good_sql([SqlAttempt(sql="SELECT x", error="boom")]) is None
    assert _last_good_sql([]) is None


def test_respond_fallback_reexec_failure_degrades(fixture_db, monkeypatch):
    """回退是锦上添花：重执行失败必须降级为原诚实失败汇报，不连累本体。"""
    from qadata.graph.nodes import make_nodes
    from qadata.types import SqlAttempt
    from tests.fakes import ScriptedLLM

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr("qadata.graph.nodes.execute_sql", boom)
    nodes = make_nodes(ScriptedLLM([]))
    state = {
        "db_path": fixture_db, "question": "q", "current_sql": None, "result": None,
        "attempts": [SqlAttempt(sql="SELECT name FROM students", row_count=1),
                     SqlAttempt(sql="SELECT bad", error="no such column: bad")],
    }
    out = nodes["respond"](state)
    assert out["answer"].failed is True
    assert "共尝试 2 次" in out["answer"].conclusion
