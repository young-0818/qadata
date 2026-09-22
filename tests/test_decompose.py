"""M11 票 03 任务分治专测（ScriptedLLM 纪律 #5，全离线零联网）。

钉面：①关态零染指（prompt 无分治尾段、模型违令产 steps 也不消费、路由/状态逐字节现状）；
②开态两步流水——步 2 prompt 含上步回执（问题/SQL/结果头字面桥）、最终 SQL 自包含
（判分端重执行语义不破坏＝本票命根子）；③共享预算一把账（len(attempts) 全题累计，
步间不各发一份——owner 裁口径）；④重试不丢桥（execute 失败清 result 后 plan.prev 仍在）；
⑤路由纯函数三分支＋steps 形状闸（2~3 条全非空才采信）。
靶单/判卷协议在册 .scratch/qadata-m11/issues/03-decomposition-probe.md（付费探针另账）。
"""
import json

from qadata.config import Settings
from qadata.graph.build import _route_after_execute, run_question
from qadata.graph.gssc import assemble, gather_understand
from qadata.graph.intent import parse_understand_response
from tests.fakes import ScriptedLLM

_S_OFF = Settings(api_key="", base_url="", model="", retry_budget=3)
_S_ON = Settings(api_key="", base_url="", model="", retry_budget=3, decompose=True)
_S_TIGHT = Settings(api_key="", base_url="", model="", retry_budget=2, decompose=True)


def _u(steps=None, q="平均成绩是多少"):
    payload = {"question": q, "intent": {"dimensions": None, "filters": None,
                                         "output_form": None, "format_constraint": None}}
    if steps:
        payload["steps"] = steps
    return json.dumps(payload, ensure_ascii=False)


def _run(fixture_db, script, settings):
    llm = ScriptedLLM(script)
    ans = run_question(fixture_db, "谁的成绩高于平均且最高", llm=llm, settings=settings)
    return llm, ans


# ── ① 关态零染指 ────────────────────────────────────────────────────

def test_off_state_violated_steps_not_consumed(fixture_db):
    """关态＝消费闸锁死：模型违令吐 steps 也只走单步旧路（prompt 零尾段、calls=3）。"""
    llm, ans = _run(fixture_db, [
        _u(steps=["第一步", "第二步"], q="改写"),
        "SELECT name FROM students WHERE id = 2",
        "Bob",
    ], _S_OFF)
    assert llm.calls == 3 and ans.failed is False
    assert "分治例外" not in llm.prompts[0]


def test_on_prompt_carries_decompose_tail(fixture_db):
    llm = ScriptedLLM([_u(q="改写"), "SELECT name FROM students", "都列了"])
    run_question(fixture_db, "谁成绩好", llm=llm, settings=_S_ON)
    assert "分治例外" in llm.prompts[0]


# ── ② 开态两步流水＋字面桥 ──────────────────────────────────────────

def test_two_step_flow_with_literal_bridge(fixture_db):
    steps = ["math 科目的平均成绩是多少", "math 成绩等于该平均的学生叫什么"]
    llm, ans = _run(fixture_db, [
        _u(steps=steps),
        "SELECT AVG(score) FROM scores WHERE subject = 'math'",   # 步1：91.75
        "SELECT name FROM students WHERE id IN (1, 2)",            # 步2（自包含 SQL）
        "相关学生列示",
    ], _S_ON)
    assert ans.failed is False
    assert llm.calls == 4  # understand + generate×2 + respond
    assert ans.sql == "SELECT name FROM students WHERE id IN (1, 2)"  # 最终 SQL＝末步、可独立重执行
    g2 = llm.prompts[2]
    assert "已完成步骤" in g2 and "91.75" in g2                        # 上步结果以字面进题
    assert "AVG(score)" in g2                                          # 上步 SQL 留痕
    assert g2.split("## 用户问题\n", 1)[1].startswith(steps[1])         # 本题面＝步 2


def test_plan_first_step_question_feeds_generate(fixture_db):
    """小库夹具不触发 LLM 选表（全量 schema 路在粗召闸之前），此钉退而核：
    步 1 题面确实顶替了改写问题进 generate（understand 返回的 question 被消费）。"""
    steps = ["math 科目的平均分", "谁等于该平均"]
    llm, _ans = _run(fixture_db, [
        _u(steps=steps, q="被无视的改写"),
        "SELECT AVG(score) FROM scores",
        "SELECT name FROM students",
        "略",
    ], _S_ON)
    assert steps[0] in llm.prompts[1] and "被无视的改写" not in llm.prompts[1]


# ── ③ 共享预算＋④ 重试不丢桥 ───────────────────────────────────────

def test_budget_shared_across_steps(fixture_db):
    """owner 裁口径：一题 retry_budget 全局共享——步 1 成功＋步 2 失败即耗尽＝判死。"""
    llm, ans = _run(fixture_db, [
        _u(steps=["math 平均分", "谁等于平均"]),
        "SELECT AVG(score) FROM scores",
        "SELECT nope FROM students",
    ], _S_TIGHT)
    assert ans.failed is True
    assert "共尝试 2 次" in ans.conclusion
    assert llm.calls == 3  # 失败路径不调 respond（永不编造）


def test_retry_after_step2_failure_keeps_bridge(fixture_db):
    llm, ans = _run(fixture_db, [
        _u(steps=["math 平均分", "谁等于平均"]),
        "SELECT AVG(score) FROM scores",     # 步1 ok（attempts=1）
        "SELECT nope FROM scores",           # 步2 执行失败（attempts=2）
        "SELECT name FROM students WHERE id = 1",  # 步2 重试（attempts=3 用尽前成功）
        "Alice 上榜",
    ], _S_ON)
    assert ans.failed is False and llm.calls == 5
    retry_prompt = llm.prompts[3]
    # 步 1 的 AVG 是全表三值 91.16…；桥在 execute 清 result 后仍从 plan.prev 供料
    assert "已完成步骤" in retry_prompt and "91.16" in retry_prompt


# ──  路由纯函数＋形状闸 ────────────────────────────────────────────

def _st(plan=None, result="R", n=1):
    return {"result": result, "attempts": [1] * n, "plan": plan}


def test_route_after_execute_plan_edges():
    plan_mid = {"steps": ["a", "b"], "i": 0}
    plan_last = {"steps": ["a", "b"], "i": 1}
    assert _route_after_execute(_st(plan_mid), 3, decompose=True) == "generate"
    assert _route_after_execute(_st(plan_mid), 3, decompose=False) == "verify"  # 关态无视 plan
    assert _route_after_execute(_st(plan_last), 3, decompose=True) == "verify"  # 末步进校验
    assert _route_after_execute(_st(None), 3, decompose=True) == "verify"       # 未拆＝现状
    assert _route_after_execute(_st(plan_mid, result=None), 1, decompose=True) == "respond"  # 失败耗尽


def test_steps_shape_gate():
    def steps_of(payload):
        return parse_understand_response(json.dumps(payload, ensure_ascii=False))[3]
    base = {"question": "改写", "intent": {}}
    assert steps_of({**base, "steps": ["一", "二"]}) == ["一", "二"]
    assert steps_of({**base, "steps": ["一"]}) is None            # 1 条＝没拆
    assert steps_of({**base, "steps": ["一", "二", "三", "四"]}) is None  # 超宽
    assert steps_of({**base, "steps": ["一", "  "]}) is None      # 含空＝半坏不采信
    assert steps_of({**base, "steps": "一二"}) is None            # 非标量形态
    assert steps_of(base) is None


# ── 装配器面：step_ctx 素材与分区（gssc 单源纪律）────────────────────

def test_gather_understand_default_off_byte_identical():
    state = {"question": "原样"}
    slots = gather_understand(state, clarify=False)
    assert slots["decompose_tail"] == ""
    assert assemble("understand", slots) == assemble("understand", dict(slots))
    on = gather_understand(state, clarify=False, decompose=True)
    assert "分治例外" in on["decompose_tail"]
