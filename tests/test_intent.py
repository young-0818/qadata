"""M5 票 02（回炉形态）：意图载体 A 的解析/回退纯函数单测＋图级行为钉死。

2026-09-09 条款④裁决（owner 签字）：三约束字段注入 generate 尾段的软用途验效不过，
注入线已拆——本文件同时钉死「generate 永远不读 intent」这条负结果防线
（防未来无意间把注入接回去）。意图保留，消费者＝值链搭车（M10 票 03；metric_match 已随 ADR-0007 退役）。

测试语义：图级一律 ScriptedLLM＋calls 计数（载体 A 零新增调用）；
prompt 形态走 RecorderLLM 旧例。
"""
import json

from qadata import run_question
from qadata.config import Settings
from qadata.graph.intent import INTENT_FIELDS, parse_understand_response
from qadata.graph.nodes import make_nodes
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


def test_parse_success_returns_question_and_four_fields():
    text = _blob(
        question="已结束合同的违约率是多少",
        dimensions=["按月份"],
        filters=["已结束"],
        output_form="百分比",
        format_constraint="保留两位小数",
    )
    q, intent, clar, _steps = parse_understand_response(text)
    assert clar is None  # 票 03：模型没产澄清键＝恒 None（宁空勿造的默认形态）
    assert q == "已结束合同的违约率是多少"
    assert intent == {
        "dimensions": ["按月份"],
        "filters": ["已结束"],
        "output_form": "百分比",
        "format_constraint": "保留两位小数",
    }


def test_parse_accepts_fenced_json_and_prose_around():
    inner = _blob()
    fenced = parse_understand_response(f"```json\n{inner}\n```")
    wrapped = parse_understand_response(f"好的：{inner} 以上。")
    assert fenced[1] is not None
    assert wrapped[1] is not None
    assert fenced[0] == wrapped[0] == "改写后的问题"
    assert fenced[2] is None and wrapped[2] is None  # 无澄清键＝第三元恒 None


def test_parse_plain_text_falls_back_to_raw():
    """解析失败回退（§3.7 既定降级语义）：原文当改写问题、意图判 null。"""
    q, intent, clar, _steps = parse_understand_response("  每个学生的平均成绩是多少  ")
    assert q == "每个学生的平均成绩是多少"
    assert intent is None and clar is None  # 回退态三元齐全：纯文本不产澄清


def test_parse_broken_json_falls_back():
    q, intent, clar, _steps = parse_understand_response('{"question": "改写", "intent": 坏掉的')
    assert q == '{"question": "改写", "intent": 坏掉的'
    assert intent is None and clar is None  # 坏 JSON 半截契约不采信


def test_parse_json_without_usable_question_falls_back():
    """改写问题拿不到＝整体失败：不发明 question，原文回退（意图判 None）。"""
    q, intent, clar, _steps = parse_understand_response('{"intent": {"output_form": "百分比"}}')
    assert q == '{"intent": {"output_form": "百分比"}}'
    assert intent is None and clar is None
    q2, intent2, clar2, _s2 = parse_understand_response('{"question": "   ", "intent": {}}')
    assert intent2 is None and clar2 is None and q2 == '{"question": "   ", "intent": {}}'


def test_parse_bad_intent_yields_all_null_not_failure():
    """改写成功但意图契约坏掉：保留改写（回退原文反而更差），四字段全 null。"""
    q, intent, clar, _steps = parse_understand_response('{"question": "改写", "intent": " nonsense"}')
    assert q == "改写"
    assert intent == _NULL_INTENT and clar is None


def test_parse_normalization_ning_kong_wu_zao():
    """宁空勿造的解析层形态：空串/空列表/缺失键→null；额外键丢弃。"""
    text = json.dumps(
        {
            "question": "改写",
            "intent": {
                "dimensions": [],
                "filters": None,
                "output_form": "百分比",
                "胡编字段": "不要",
            },
        },
        ensure_ascii=False,
    )
    _, intent, _clar, _steps = parse_understand_response(text)
    assert intent == {
        "dimensions": None,
        "filters": None,
        "output_form": "百分比",
        "format_constraint": None,
    }


def test_parse_coerces_list_fields_from_scalars():
    """模型把列表字段写成标量/混入数字：宽容收编为字符串列表，不判失败。"""
    text = _blob(
        question="q",
        dimensions="按月份",
        filters=[2023, "", "  已结束  ", {"坏": "元素"}],
    )
    _, intent, _clar, _steps = parse_understand_response(text)
    assert intent["dimensions"] == ["按月份"]
    assert intent["filters"] == ["2023", "已结束"]


# ── M8 票 03：第三元（澄清问句，宁空勿造在解析层的形态）─────────────


def _full(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


def test_parse_clarification_returns_third_element():
    text = _full({"question": "哪个指标的口径？",
                  "intent": {"output_form": "百分比"},
                  "clarification": "「表现」指成绩还是违约率？"})
    q, intent, clar, _steps = parse_understand_response(text)
    assert q == "哪个指标的口径？"
    assert intent["output_form"] == "百分比"
    assert clar == "「表现」指成绩还是违约率？"


def test_parse_clarification_ning_kong_wu_zao_forms():
    """宁空勿造：缺失/空串/空白/非字符串一律 None——「完全歧义」之外不产。"""
    for bad in (None, "", "   ", True, 123, [], {}):
        payload = {"question": "改写", "intent": {}}
        if bad is not None:
            payload["clarification"] = bad
        _, intent, clar, _steps = parse_understand_response(_full(payload))
        assert clar is None, f"坏形态 {bad!r} 不得被采信"
        assert intent == _NULL_INTENT  # 顺带钉：四字段照常归一


def test_parse_clarification_orthogonal_to_question_failure():
    """模型丢改写只留澄清问：question 原文回退（不发明），澄清照常收——
    路由端 answer 直达 END，回退出来的 JSON 原文永不喂给 generate。"""
    text = _full({"clarification": "按入学年还是毕业年算？"})
    q, intent, clar, _steps = parse_understand_response(text)
    assert intent is None
    assert clar == "按入学年还是毕业年算？"
    assert q == text


def test_parse_fallback_states_never_produce_clarification():
    """回退态不产澄清：纯文本、坏 JSON 的第三元恒 None（半截契约不采信）。"""
    assert parse_understand_response("到底算哪个？请补充")[2] is None
    assert parse_understand_response('{"clarification": "半截"')[2] is None


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


# （test_understand_prompt_carries_evidence 已随 ADR-0008 退役：背景信息段不复存在，
#   "state 残键零注入"防回吹钉见 tests/test_gssc.py 双跑金标准。）

def test_intent_success_path_zero_extra_calls(fixture_db):
    """载体 A 零新增调用：意图成功路径 calls 与纯文本回退一致（3＝understand+generate+respond）。"""
    llm = ScriptedLLM(
        [_blob(question="谁成绩最好", output_form="计数"),
         "SELECT name FROM students WHERE id = 1", "Alice"]
    )
    ans = run_question(fixture_db, "谁最好", llm=llm, settings=_S)
    assert ans.failed is False
    assert llm.calls == 3


def test_generate_never_reads_intent(fixture_db):
    """④负结果防线（钉死）：意图约束字段再满，generate prompt 也与无意图形态逐字节
    一致——「注入尾段喂 generate」软用途已判负拆除，防止无意间接回去。"""
    prompts = []
    for understand_reply in ("纯文本改写",
                             _blob(question="纯文本改写", output_form="计数形态",
                                   format_constraint="保留两位小数",
                                   evidence_terms=["全名 = first_name, last_name"])):
        script = [understand_reply, "SELECT name FROM students WHERE id = 1", "结论"]
        recorder = RecorderLLM(script)
        run_question(fixture_db, "谁最好", llm=recorder, settings=_S)
        prompts.append(recorder.prompts[1])
    assert prompts[0] == prompts[1]
    assert "题面明示约束" not in prompts[1]
    assert "计数形态" not in prompts[1] and "全名 = first_name" not in prompts[1]
