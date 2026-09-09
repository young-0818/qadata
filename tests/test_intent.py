"""M5 票 02（回炉形态）：意图载体 A 的解析/回退纯函数单测＋图级行为钉死。

2026-09-09 条款④裁决（owner 签字）：三约束字段注入 generate 尾段的软用途验效不过，
注入线已拆——本文件同时钉死「generate 永远不读 intent」这条负结果防线
（防未来无意间把注入接回去）。意图保留，唯一消费者＝metric_match（票 05）。

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


# ── 图级（缝 A run_question）：calls 计数与负结果防线 ──────────────


def test_intent_success_path_zero_extra_calls(fixture_db):
    """载体 A 零新增调用：意图成功路径 calls 与纯文本回退一致（3＝understand+generate+respond）。"""
    llm = ScriptedLLM(
        [_blob(question="谁成绩最好", metric_mention="成绩"),
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
        run_question(fixture_db, "谁最好", evidence="口径说明", llm=recorder, settings=_S)
        prompts.append(recorder.prompts[1])
    assert prompts[0] == prompts[1]
    assert "题面明示约束" not in prompts[1]
    assert "计数形态" not in prompts[1] and "全名 = first_name" not in prompts[1]
