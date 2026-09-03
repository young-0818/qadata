import httpx
import openai
import pytest
from langchain_openai import ChatOpenAI

from qadata.config import Settings
from qadata.llm.gateway import build_llm, invoke_with_backoff


def test_build_llm_uses_settings():
    s = Settings(api_key="sk-test", base_url="https://api.deepseek.com", model="deepseek-chat")
    llm = build_llm(s)
    assert isinstance(llm, ChatOpenAI)
    assert llm.temperature == 0
    assert llm.openai_api_base == "https://api.deepseek.com"


class FlakyLLM:
    def __init__(self, fail_times, exc):
        self.fail_times = fail_times
        self.exc = exc
        self.calls = 0

    def invoke(self, prompt):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self.exc

        class R:
            content = "ok"

        return R()


def _rate_limit_error():
    resp = httpx.Response(429, request=httpx.Request("POST", "https://example.com"))
    return openai.RateLimitError("限流", response=resp, body=None)


def _auth_error():
    resp = httpx.Response(401, request=httpx.Request("POST", "https://example.com"))
    return openai.AuthenticationError("密钥无效", response=resp, body=None)


def test_backoff_retries_rate_limit_then_succeeds():
    sleeps = []
    llm = FlakyLLM(fail_times=2, exc=_rate_limit_error())
    out = invoke_with_backoff(llm, "p", base_delay=1.0, sleep=sleeps.append)
    assert out.content == "ok" and llm.calls == 3
    assert len(sleeps) == 2
    assert 0.5 <= sleeps[0] <= 1.0   # 1×2^0×[0.5,1.0)
    assert 1.0 <= sleeps[1] <= 2.0   # 1×2^1×[0.5,1.0)


def test_backoff_exhausted_raises():
    llm = FlakyLLM(fail_times=99, exc=_rate_limit_error())
    with pytest.raises(openai.RateLimitError):
        invoke_with_backoff(llm, "p", max_retries=2, sleep=lambda _: None)
    assert llm.calls == 3  # 1 次原始 + 2 次重试


def test_auth_error_not_retried():
    llm = FlakyLLM(fail_times=99, exc=_auth_error())
    with pytest.raises(openai.AuthenticationError):
        invoke_with_backoff(llm, "p", sleep=lambda _: None)
    assert llm.calls == 1  # 401 立即失败，不浪费重试


def test_connection_error_retried():
    llm = FlakyLLM(fail_times=1, exc=ConnectionError("网络抖动"))
    out = invoke_with_backoff(llm, "p", sleep=lambda _: None)
    assert out.content == "ok" and llm.calls == 2
