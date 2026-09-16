"""M8 票 08 思考流专测：understand/generate 流式旁路推 thinking 帧。

钉四件事（对应票面）：
① thinking 帧转发与归属——on_event 在场且模型有思考时逐 chunk 出帧（形状 {node,kind,text}
   单源），增量拼接还原思考全文，且只出现在 understand/generate（respond 等走 invoke）；
② 单流截断 ≤2000 字防灌 DOM；
③ 关态逐字节姊妹钉——on_event=None 时流式旁路根本未触发（ScriptedLLM.stream_used 恒 0），
   CLI/eval 调用面零变化（on_event 纪律第三例，与票 03/06 同源）；
④ 退避窄化——仅首 token 前连接错重开流，首 token 后断流／非重试错＝如实上抛。
真端点 reasoning_content 形态由探针 runs/m8-budget.md 留档（本文件全走假模型零花费）。
"""
import pytest

from qadata.config import Settings
from qadata.graph.build import run_question
from qadata.llm.tracing import timed_stream
from tests.fakes import FlakyStreamLLM, ScriptedLLM

_S = Settings(api_key="", base_url="", model="test-model", retry_budget=3)
_GOOD = "SELECT name FROM students WHERE id = 2"
_THINK_KEYS = {"node", "kind", "text"}


# ── graph 接线：thinking 帧转发／归属／截断／关态 ──────────────────────


def _thinking(frames):
    return [f for f in frames if f.get("kind") == "thinking"]


def test_thinking_frames_forwarded_and_attributed(fixture_db):
    """understand 配思考脚本、generate 不配——thinking 帧只属 understand，增量拼回全文。"""
    reasoning = "先想清楚要查哪张表哪一列"
    frames: list[dict] = []
    llm = ScriptedLLM(['{"question":"查成绩"}', _GOOD, "Bob 88"],
                      reasonings=[reasoning, None, None])
    answer = run_question(fixture_db, "谁成绩最好", llm=llm, settings=_S,
                          on_event=frames.append)
    think = _thinking(frames)
    assert think, "on_event 在场＋模型有思考＝thinking 帧必须出现"
    assert all(set(f) == _THINK_KEYS for f in think), think[0]
    assert all(f["node"] == "understand" for f in think)
    assert "".join(f["text"] for f in think) == reasoning  # 逐 chunk 拼接无损
    assert answer.failed is False
    assert llm.calls == 3  # stream 与 invoke 同计一次调用（calls 语义不变）


def test_thinking_truncated_at_cap(fixture_db):
    """单流累计截断 ≤2000 字（防灌 DOM）——再长的思考也只推 2000 字。"""
    frames: list[dict] = []
    llm = ScriptedLLM(['{"question":"查成绩"}', _GOOD, "Bob 88"],
                      reasonings=["想" * 5000, None, None])
    run_question(fixture_db, "谁成绩最好", llm=llm, settings=_S, on_event=frames.append)
    assert sum(len(f["text"]) for f in _thinking(frames)) == 2000


def test_off_state_never_streams(fixture_db):
    """关态姊妹钉：不传 on_event → 恒走 invoke，流式旁路一次未触发（CLI/eval 零变化）。"""
    llm = ScriptedLLM(['{"question":"查成绩"}', _GOOD, "Bob 88"],
                      reasonings=["关态不该出现的思考", None, None])
    run_question(fixture_db, "谁成绩最好", llm=llm, settings=_S)
    assert llm.stream_used == 0
    assert llm.calls == 3  # 走 invoke 仍三次


def test_only_understand_and_generate_stream(fixture_db):
    """开态 stream_used 计数：understand＋generate 各一流式，respond 走 invoke（不推思考）。"""
    llm = ScriptedLLM(['{"question":"查成绩"}', _GOOD, "Bob 88"])
    run_question(fixture_db, "谁成绩最好", llm=llm, settings=_S, on_event=lambda _: None)
    assert llm.stream_used == 2  # 恰 understand＋generate，respond 不流


# ── timed_stream 退避窄化（tracing 层单元；假模型＝tests/fakes::FlakyStreamLLM）────


def test_retry_before_first_token():
    llm = FlakyStreamLLM(pre_fail=1)
    out = timed_stream(llm, "p", "generate", None, sleep=lambda *_: None)
    assert out == "hello" and llm.attempts == 2  # 一帧未发时重开一次即成功


def test_raise_after_first_token():
    llm = FlakyStreamLLM(mid_fail=1)
    with pytest.raises(ConnectionError):
        timed_stream(llm, "p", "generate", None, sleep=lambda *_: None)
    assert llm.attempts == 1  # 已吐 chunk＝不重连（帧不可回收）


def test_no_retry_on_nonretryable():
    llm = FlakyStreamLLM(pre_fail=1, exc=ValueError)  # 非连接/状态错＝立即失败
    with pytest.raises(ValueError):
        timed_stream(llm, "p", "generate", None, sleep=lambda *_: None)
    assert llm.attempts == 1


def test_stream_token_lands_in_sink_and_frames():
    """终帧聚合：token 进 sink（费用卡数据源）＋账本，形状同 timed_invoke（零新增调用）。"""
    u = {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}
    sink = {"tin": 0, "tout": 0}
    frames: list[dict] = []
    out = timed_stream(FlakyStreamLLM(usage=u, think="想想"), "p", "understand", None,
                       sink=sink, on_event=frames.append)
    assert out == "hello"
    assert sink == {"tin": 7, "tout": 3}
    assert [f["kind"] for f in frames] == ["thinking"]
