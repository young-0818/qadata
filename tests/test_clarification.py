"""M8 票 03 澄清回合专测：默认关的保险丝，不是主菜。

钉死面（票面验收清单对位）：
- understand 出口＝澄清时写 answer（conclusion=澄清语、clarification=同文、failed=False），
  路由纯函数首判 answer→END（直测在 tests/test_routing.py 补针）；
  路由 map 装配面＝END 分支随开关才挂（本文件编译图直测）；
- happy＋澄清＝**1 call** 直达 END（不进 explore/generate/respond，零沙箱零账本）；
- 关态逐字节＝flag 关时 understand prompt 与今日一致（无指令段）、路由 map 不含 END、
  模型违令产出澄清键也不消费（关态不可达双保险）；
- 防循环标记闸＝「补充说明：」双闸：带标记 prompt 不加指令段（指令侧）＋
  节点侧机制版复闸（M5 ⑨闸教训——模型违令也不采信）。
测试语义：一律 ScriptedLLM＋calls 计数（纪律⑤）；prompt 形态走 RecorderLLM。
"""
import json
from dataclasses import replace

from qadata import run_question
from qadata.config import Settings
from qadata.graph.build import build_graph
from qadata.graph.nodes import make_nodes
from qadata.graph.prompts import SUPPLEMENT_MARK, understand_prompt
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
                   "intent": dict.fromkeys(("metric_mention", "dimensions", "filters",
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
    """关态路由 map 与今日逐分支一致：END 出边根本不装配；开态才挂（含与
    metric_layer 同开时三出口并存）。"""
    assert "__end__" not in _understand_targets(_S_OFF)
    assert _understand_targets(_S_OFF) == {"explore", "metric_match"}
    assert "__end__" in _understand_targets(_S_ON)
    both = replace(_S_ON, metric_layer=True)
    assert _understand_targets(both) == {"__end__", "explore", "metric_match"}
