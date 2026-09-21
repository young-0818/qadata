"""M8 票 03 澄清回合专测：默认关的保险丝，不是主菜。

钉死面（票面验收清单对位）：
- 无 thread 形态（CLI/评测/单轮）＝understand 出口写 answer（conclusion=澄清语、
  clarification=同文、failed=False），路由纯函数首判 answer→END（直测在
  tests/test_routing.py 补针）；路由 map 装配面＝END 分支随开关才挂（本文件编译图直测）；
- happy＋澄清＝**1 call** 直达 END（不进 explore/generate/respond，零沙箱零账本）；
- **经典 HITL（owner 改判 2026-09-16）**＝thread＋checkpointer 成对时节点内 interrupt()
  暂停（同样 1 call、__interrupt__ 面收成澄清 Answer），resume_question 续跑
  （understand 重放＝既定代价，全程 4 call 省于无状态往返的 6），合成公式
  compose_supplement 单源（HITL 恢复态与 web 归档共用）；
- 关态逐字节＝flag 关时 understand prompt 与今日一致（无指令段）、路由 map 不含 END、
  模型违令产出澄清键也不消费（关态不可达双保险）；
- 防循环标记闸＝「补充说明：」双闸：带标记 prompt 不加指令段（指令侧）＋
  节点侧机制版复闸（M5 ⑨闸教训——模型违令也不采信）。
测试语义：一律 ScriptedLLM＋calls 计数（纪律⑤）；prompt 形态走 RecorderLLM。
"""
import json

from langgraph.checkpoint.memory import MemorySaver

from qadata import run_question
from qadata.config import Settings
from qadata.graph.build import build_graph, resume_question
from qadata.graph.nodes import make_nodes
from qadata.graph.prompts import SUPPLEMENT_MARK, compose_supplement, understand_prompt
from qadata.types import Answer
from tests.conftest import RecorderLLM
from tests.fakes import ScriptedLLM

_S_OFF = Settings(api_key="", base_url="", model="", retry_budget=3)
_S_ON = Settings(api_key="", base_url="", model="", retry_budget=3, clarification=True)

_ASK = "「表现」指成绩还是违约率？"
_BLOB = json.dumps({"question": "学生的表现如何", "intent": {}, "clarification": _ASK},
                   ensure_ascii=False)
_HAPPY = ["改写后的问题", "SELECT name FROM students WHERE id = 2", "Bob"]


def test_understand_node_writes_answer_on_clarification():
    llm = ScriptedLLM([_BLOB])
    out = make_nodes(llm, settings=_S_ON)["understand"]({"question": "学生表现如何"})
    ans = out["answer"]
    assert isinstance(ans, Answer)
    assert ans.conclusion == _ASK and ans.clarification == _ASK
    assert ans.failed is False and ans.sql is None and ans.result is None
    assert out["original_question"] == "学生表现如何"  # 改写与意图照常入账（END 后无人读）
    assert "attempts" not in out  # 零账本——不进重试环
    assert llm.calls == 1


def test_understand_node_flag_off_ignores_clarification_key():
    """关态保险丝不起爆：模型违令产出澄清键也不写 answer，出口与今日逐字一致。"""
    out = make_nodes(ScriptedLLM([_BLOB]), settings=_S_OFF)["understand"](
        {"question": "学生表现如何"})
    assert out == {"original_question": "学生表现如何", "question": "学生的表现如何",
                   "intent": dict.fromkeys(("dimensions", "filters",
                                            "output_form", "format_constraint",
                                            "evidence_terms"))}
    assert "answer" not in out


def test_understand_node_marker_gate_not_consumed():
    """防循环机制版复闸：续轮题面带「补充说明：」标记，模型违令再产澄清也不消费
    （出口走常规路径，续轮必须作答——死循环在代码层掐断，不靠指令自觉）。"""
    assert SUPPLEMENT_MARK in ("学生表现如何补充说明：" + _ASK + " 指成绩")
    out = make_nodes(ScriptedLLM([_BLOB]), settings=_S_ON)["understand"](
        {"question": "学生表现如何补充说明：" + _ASK + " 指成绩"})
    assert "answer" not in out
    assert out["question"] == "学生的表现如何"  # 常规出口原样


def test_clarify_tail_only_when_on_and_no_marker():
    """指令段形态：关态/带标记态逐字节等于参数加入前；开态无标记才尾追。"""
    base = understand_prompt("学生表现如何", "口径")
    assert understand_prompt("学生表现如何", "口径", clarify=False) == base
    composed = "学生表现如何补充说明：" + _ASK + " 指成绩"
    assert understand_prompt(composed, "口径", clarify=True) == understand_prompt(
        composed, "口径", clarify=False)  # 标记闸＝不加段（与关态同形）
    on = understand_prompt("学生表现如何", "口径", clarify=True)
    assert on.startswith(base) and "澄清例外" in on and '"clarification"' in on
    assert "不构成" in on  # 负清单在场（值写法/列归属/形态/时间都轮不到回问）


def test_graph_clarification_is_one_call_to_end(fixture_db):
    """happy＋澄清＝1 call：直达 END，不进 explore（零 schema 调用）零沙箱。"""
    llm = ScriptedLLM([_BLOB])
    ans = run_question(fixture_db, "学生表现如何", llm=llm, settings=_S_ON)
    assert llm.calls == 1
    assert ans.clarification == _ASK and ans.failed is False
    assert "【结论】" not in ans.conclusion  # 澄清语原样成文，不走三节组装


def test_graph_on_flag_normal_path_unaffected(fixture_db):
    """开态非澄清题＝与关态同调用数同形态（保险丝只在完全歧义时熔断）。"""
    llm = ScriptedLLM(_HAPPY)
    ans = run_question(fixture_db, "Bob 成绩如何", llm=llm, settings=_S_ON)
    assert ans.failed is False and llm.calls == 3
    assert ans.clarification is None


def test_graph_marker_turn_must_answer(fixture_db):
    """续轮全链闭环：带标记的合成问句即使模型再产澄清，也被复闸拦下走常规作答路
    （understand→explore→generate→respond＝3 call，绝不二次回问）。"""
    composed = "学生表现如何补充说明：" + _ASK + " 指成绩"
    llm = ScriptedLLM([_BLOB] + _HAPPY[1:])
    ans = run_question(fixture_db, composed, llm=llm, settings=_S_ON)
    assert llm.calls == 3
    assert ans.clarification is None and ans.failed is False


def test_prompt_off_state_byte_identical_in_graph(fixture_db):
    """关态逐字节：flag 关的 understand prompt ＝ understand_prompt(默认参)（今日形态），
    且与开态差异只在尾段（前缀缓存面不动）。"""
    recorder_off = RecorderLLM(_HAPPY)
    run_question(fixture_db, "Bob 成绩如何", evidence="口径A", llm=recorder_off,
                 settings=_S_OFF)
    assert recorder_off.prompts[0] == understand_prompt("Bob 成绩如何", "口径A")
    assert "澄清例外" not in recorder_off.prompts[0]


def _understand_targets(settings) -> set:
    """编译图的 understand 出边集（装配面直测，不止纯函数嘴说）。"""
    pub = build_graph(ScriptedLLM([]), settings=settings).get_graph()
    return {e.target for e in pub.edges if e.source == "understand"}


def test_route_map_end_branch_assembled_only_with_flag():
    """关态路由 map 与今日逐分支一致：END 出边根本不装配；开态才挂。
    （metric_match 出口已随 ADR-0007 退役，关态出边集＝{explore}。）"""
    assert "__end__" not in _understand_targets(_S_OFF)
    assert _understand_targets(_S_OFF) == {"explore"}
    assert "__end__" in _understand_targets(_S_ON)


# ── 经典 HITL（owner 改判 2026-09-16）：interrupt 暂停／Command(resume) 续跑────


def test_compose_supplement_single_source_formula():
    """合成公式单源（HITL 恢复态与 web 归档共用）：原问＋标记＋澄清问＋答，去空白。"""
    assert compose_supplement("Q", "A？", " 入学年 ") == "Q补充说明：A？ 入学年"
    assert SUPPLEMENT_MARK in compose_supplement("Q", "A？", "答")  # 防循环闸同源


def test_hitl_pause_is_one_call_clarification_answer(fixture_db):
    """成对参数下澄清＝节点内 interrupt() 暂停：invoke 正常返回（零线程挂等），
    __interrupt__ 面收成澄清 Answer——与直 END 形态同价（1 call、failed=False）。"""
    llm = ScriptedLLM([_BLOB])
    ans = run_question(fixture_db, "学生表现如何", llm=llm, settings=_S_ON,
                       thread_id="t", checkpointer=MemorySaver())
    assert ans.clarification == _ASK and ans.conclusion == _ASK
    assert ans.failed is False and ans.sql is None
    assert llm.calls == 1


def test_hitl_resume_composes_marker_and_answers(fixture_db):
    """续跑闭环：Command(resume) 后 understand 重放（＝2 次调用的既定代价，
    全程 4 call < 无状态往返 6 call），合成全句带标记进 generate（复闸同源），
    最终正常作答、澄清位归零。"""
    cp = MemorySaver()
    llm = ScriptedLLM([_BLOB, _BLOB] + _HAPPY[1:])
    run_question(fixture_db, "学生表现如何", llm=llm, settings=_S_ON,
                 thread_id="t", checkpointer=cp)
    ans = resume_question("t", "指成绩", llm=llm, settings=_S_ON, checkpointer=cp)
    assert ans.failed is False and ans.clarification is None
    assert llm.calls == 4
    # 暂停态存的是原始题面（探针已证），重放合成后 generate 吃到的＝带标记全句
    assert compose_supplement("学生表现如何", _ASK, "指成绩") in llm.prompts[2]


def test_hitl_unpaired_args_fall_back_to_direct_end(fixture_db):
    """成对纪律在守护闸口守死：只给 thread 不给 checkpointer（或反之）＝不配对，
    按直 END 形态跑（调用方无需自行配平，langgraph 的拒收面不外露为失败）。"""
    llm = ScriptedLLM([_BLOB])
    ans = run_question(fixture_db, "学生表现如何", llm=llm, settings=_S_ON, thread_id="t")
    assert ans.clarification == _ASK and llm.calls == 1


def test_hitl_off_flag_ignores_thread(fixture_db):
    """关态双保险：flag 关即使误配成对参数，也不暂停不澄清——prompt 无尾段、
    解析面照常、出口与今日逐字节一致（3 call 常规链）。"""
    llm = ScriptedLLM(["改写", "SELECT name FROM students WHERE id = 2", "Bob"])
    ans = run_question(fixture_db, "Bob 成绩如何", llm=llm, settings=_S_OFF,
                       thread_id="t", checkpointer=MemorySaver())
    assert ans.failed is False and ans.clarification is None and llm.calls == 3


def test_resume_unknown_thread_honest_failure():
    """续跑不存在的 thread（重启后指针过期/乱序调用）＝守护收敛为诚实失败，
    永不编造、不裸抛。"""
    ans = resume_question("ghost-thread", "答", llm=ScriptedLLM([]), settings=_S_ON,
                          checkpointer=None)
    assert ans.failed is True and "未能完成查询" in ans.conclusion
