import pytest

from tests.fakes import FakeMsg, ScriptedLLM


def test_scripted_llm_returns_in_order():
    llm = ScriptedLLM(["第一", "第二"])
    assert llm.invoke("a").content == "第一"
    assert llm.invoke("b").content == "第二"
    assert llm.calls == 2
    assert llm.prompts == ["a", "b"]


def test_scripted_llm_overrun_raises_instead_of_cycling():
    """M1 教训：FakeListChatModel 耗尽后循环，掩盖了调用次数错误。ScriptedLLM 必须显式报错。"""
    llm = ScriptedLLM(["仅一条"])
    llm.invoke("x")
    with pytest.raises(AssertionError, match="超出脚本"):
        llm.invoke("y")


def test_fake_msg_usage_optional():
    assert FakeMsg("hi").usage_metadata is None
    assert FakeMsg("hi", {"input_tokens": 1}).usage_metadata["input_tokens"] == 1
