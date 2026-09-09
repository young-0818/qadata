"""M5 票 02：意图结构化（载体 A）——解析/回退纯函数单测＋图级行为钉死。

测试语义：图级一律 ScriptedLLM＋calls 计数（载体 A 零新增调用）；
prompt 注入形态走 RecorderLLM 旧例；逐字节防污染＝无约束时 generate prompt
与意图接线前完全一致。
"""
import json

from qadata.config import Settings
from qadata.graph.intent import (
    INTENT_FIELDS,
    format_intent_constraints,
    parse_understand_response,
)
from qadata.graph.nodes import make_nodes
from qadata.graph.prompts import SYSTEM_RULES, sql_prompt
from tests.conftest import RecorderLLM
from tests.fakes import ScriptedLLM

_S = Settings(api_key="", base_url="", model="", retry_budget=3)


def _blob(question="改写后的问题", **intent_fields):
    """构造 understand 的载体 A 回复：改写问题＋意图 JSON。"""
    return json.dumps(
        {"question": question, "intent": intent_fields}, ensure_ascii=False
    )


_NULL_INTENT = dict.fromkeys(INTENT_FIELDS)


# ── 纯函数：parse_understand_response ──────────────────────────────


def test_parse_success_returns_question_and_six_fields():
    text = _blob(
        question="已结束合同的违约率是多少",
        metric_mention="违约率",
        dimensions=["按月份"],
        filters=["已结束"],
        output_form="百分比",
        format_constraint="保留两位小数",
        evidence_terms=["违约 = status 'B'"],
    )
    q, intent = parse_understand_response(text)
    assert q == "已结束合同的违约率是多少"
    assert intent == {
        "metric_mention": "违约率",
        "dimensions": ["按月份"],
        "filters": ["已结束"],
        "output_form": "百分比",
        "format_constraint": "保留两位小数",
        "evidence_terms": ["违约 = status 'B'"],
    }


def test_parse_accepts_fenced_json_and_prose_around():
    inner = _blob()
    fenced = parse_understand_response(f"```json\n{inner}\n```")
    wrapped = parse_understand_response(f"好的：{inner} 以上。")
    assert fenced[1] is not None
    assert wrapped[1] is not None
    assert fenced[0] == wrapped[0] == "改写后的问题"


def test_parse_plain_text_falls_back_to_raw():
    """解析失败回退（§3.7 既定降级语义）：原文当改写问题、意图判 null。"""
    q, intent = parse_understand_response("  每个学生的平均成绩是多少  ")
    assert q == "每个学生的平均成绩是多少"
    assert intent is None


def test_parse_broken_json_falls_back():
    q, intent = parse_understand_response('{"question": "改写", "intent": 坏掉的')
    assert q == '{"question": "改写", "intent": 坏掉的'
    assert intent is None


def test_parse_json_without_usable_question_falls_back():
    """改写问题拿不到＝整体失败：不发明 question，原文回退。"""
    q, intent = parse_understand_response('{"intent": {"output_form": "百分比"}}')
    assert q == '{"intent": {"output_form": "百分比"}}'
    assert intent is None
    q2, intent2 = parse_understand_response('{"question": "   ", "intent": {}}')
    assert intent2 is None and q2 == '{"question": "   ", "intent": {}}'


def test_parse_bad_intent_yields_all_null_not_failure():
    """改写成功但意图契约坏掉：保留改写（回退原文反而更差），六字段全 null。"""
    q, intent = parse_understand_response('{"question": "改写", "intent": " nonsense"}')
    assert q == "改写"
    assert intent == _NULL_INTENT


def test_parse_normalization_ning_kong_wu_zao():
    """宁空勿造的解析层形态：空串/空列表/缺失键→null；额外键丢弃。"""
    text = json.dumps(
        {
            "question": "改写",
            "intent": {
                "metric_mention": "  ",
                "dimensions": [],
                "filters": None,
                "output_form": "百分比",
                "胡编字段": "不要",
            },
        },
        ensure_ascii=False,
    )
    _, intent = parse_understand_response(text)
    assert intent == {
        "metric_mention": None,
        "dimensions": None,
        "filters": None,
        "output_form": "百分比",
        "format_constraint": None,
        "evidence_terms": None,
    }


def test_parse_coerces_list_fields_from_scalars():
    """模型把列表字段写成标量/混入数字：宽容收编为字符串列表，不判失败。"""
    text = _blob(
        question="q",
        dimensions="按月份",
        filters=[2023, "", "  已结束  ", {"坏": "元素"}],
    )
    _, intent = parse_understand_response(text)
    assert intent["dimensions"] == ["按月份"]
    assert intent["filters"] == ["2023", "已结束"]


# ── 纯函数：format_intent_constraints（generate 尾段） ─────────────


def test_constraints_empty_for_none_or_all_null():
    assert format_intent_constraints(None) == ""
    assert format_intent_constraints(_NULL_INTENT) == ""


def test_constraints_only_nonempty_three_fields():
    """仅 output_form/format_constraint/evidence_terms 三约束字段进尾段；
    非空才注入（宁空勿造）；metric_mention/dimensions/filters 归 metric_match 消费。"""
    intent = {
        "metric_mention": "违约率",
        "dimensions": ["按月份"],
        "filters": ["已结束"],
        "output_form": "百分比",
        "format_constraint": None,
        "evidence_terms": ["全名 = first_name, last_name", "收入 > 40"],
    }
    s = format_intent_constraints(intent)
    assert "百分比" in s
    assert "全名 = first_name, last_name" in s and "收入 > 40" in s
    assert "保留两位" not in s
    assert "违约率" not in s and "按月份" not in s and "已结束" not in s
    assert "格式约束" not in s  # null 字段连标签都不出现


# ── 节点级：understand 回退不写 attempts、不烧预算 ────────────────


def test_understand_fallback_output_shape():
    nodes = make_nodes(ScriptedLLM(["纯文本改写"]))
    out = nodes["understand"]({"question": "原始问题"})
    assert out == {"original_question": "原始问题", "question": "纯文本改写", "intent": None}
    assert "attempts" not in out  # 不写账本＝不烧重试预算


def test_understand_success_output_shape_and_single_call():
    llm = ScriptedLLM([_blob(output_form="百分比")])
    out = make_nodes(llm)["understand"]({"question": "原始问题", "evidence": "口径 A"})
    assert out["question"] == "改写后的问题"
    assert out["intent"]["output_form"] == "百分比"
    assert llm.calls == 1  # 载体 A：改写＋意图同一次调用


def test_understand_prompt_carries_evidence():
    recorder = RecorderLLM(["x"])
    make_nodes(recorder)["understand"]({"question": "谁有全名", "evidence": "全名指 first_name, last_name"})
    p = recorder.prompts[0]
    assert "全名指 first_name, last_name" in p  # 无 evidence 明示就抽不出 evidence_terms
    for field in INTENT_FIELDS:
        assert field in p  # 六字段契约写进 prompt
    assert "宁空勿造" in p or "明示" in p


# ── 图级（缝 A run_question）：calls 计数与注入形态 ────────────────


def test_intent_success_path_zero_extra_calls(fixture_db):
    """载体 A 零新增调用：意图成功路径 calls 与纯文本回退一致（3＝understand+generate+respond）。"""
    from qadata import run_question

    llm = ScriptedLLM(
        [_blob(question="谁成绩最好", metric_mention="成绩"),
         "SELECT name FROM students WHERE id = 1", "Alice"]
    )
    ans = run_question(fixture_db, "谁最好", llm=llm, settings=_S)
    assert ans.failed is False
    assert llm.calls == 3


def test_intent_constraints_reach_generate_tail(fixture_db):
    """注入形态断言（RecorderLLM）：仅非空字段、动态段位置（SYSTEM_RULES 前端不动、
    用户问题之后、最终指令之前）。"""
    from qadata import run_question

    recorder = RecorderLLM(
        [_blob(question="俱乐部全名和学院",
               metric_mention=None, dimensions=None, filters=None,
               output_form="列出全名与学院两列", format_constraint=None,
               evidence_terms=["全名 = first_name, last_name"]),
         "SELECT 1", "结论"]
    )
    run_question(fixture_db, "哪个社团", evidence="全名指 first_name, last_name",
                 llm=recorder, settings=_S)
    p = recorder.prompts[1]  # generate prompt
    assert p.startswith(SYSTEM_RULES)
    assert "题面明示约束" in p
    assert "列出全名与学院两列" in p
    assert "全名 = first_name, last_name" in p
    assert "格式约束" not in p  # null 字段不进尾段
    assert p.index("题面明示约束") > p.index("## 用户问题")
    assert p.index("题面明示约束") < p.index("输出一条 SQL：")


def test_generate_prompt_byte_identical_without_constraints(fixture_db):
    """防污染回归（钉死）：解析失败回退与六字段全空两种形态的 generate prompt
    逐字节一致（intent 缺键形态由 sql_prompt 默认参数测试钉）——意图接线对无约束题零影响。"""
    from qadata import run_question

    prompts = []
    # 两种端到端形态：解析失败回退 / 六字段全空——改写问题取同一个值
    for understand_reply in ("纯文本改写", _blob(question="纯文本改写")):
        script = [understand_reply, "SELECT name FROM students WHERE id = 1", "结论"]
        recorder = RecorderLLM(script)
        run_question(fixture_db, "谁最好", evidence="口径说明", llm=recorder, settings=_S)
        prompts.append(recorder.prompts[1])
    assert prompts[0] == prompts[1]
    assert "题面明示约束" not in prompts[0]


# ── sql_prompt 集成（默认参数防污染） ──────────────────────────────


def test_sql_prompt_intent_default_byte_identical():
    legacy = sql_prompt("S", "E", "Q")
    assert sql_prompt("S", "E", "Q", intent=None) == legacy
    assert sql_prompt("S", "E", "Q", intent=_NULL_INTENT) == legacy
    with_c = sql_prompt("S", "E", "Q", intent={**_NULL_INTENT, "output_form": "百分比"})
    assert with_c.startswith(SYSTEM_RULES) and "百分比" in with_c
    # 与失败历史的相对位置：约束段在前，历史段紧邻最终指令
    both = sql_prompt("S", "E", "Q", history="## 之前的失败尝试\n尝试 1：x",
                      intent={**_NULL_INTENT, "output_form": "百分比"})
    assert both.index("题面明示约束") < both.index("之前的失败尝试")
