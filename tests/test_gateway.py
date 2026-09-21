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


def test_backoff_acquires_limiter_before_each_attempt():
    """限速与重试叠加：每次真实调用（含重试）前都先 acquire。"""
    acquires = []

    class FakeLimiter:
        def acquire(self):
            acquires.append(1)

    llm = FlakyLLM(fail_times=1, exc=_rate_limit_error())
    out = invoke_with_backoff(llm, "p", sleep=lambda _: None, limiter=FakeLimiter())
    assert out.content == "ok"
    assert len(acquires) == 2  # 两次尝试各 acquire 一次


def test_backoff_without_limiter_unchanged():
    """不给 limiter 时行为与旧版一致（无 acquire 动作）。"""
    llm = FlakyLLM(fail_times=0, exc=None)
    out = invoke_with_backoff(llm, "p", sleep=lambda _: None)
    assert out.content == "ok" and llm.calls == 1


def test_embedder_chunks_at_batch_max(monkeypatch):
    """切批钉（票 07 端点实拍 ≤20/请求）：25 条→20+5 两批、顺序拼接、限速逐批领、
    空料零请求。「一次向量化调用＝档面本体」是逻辑记法，HTTP 批数是实现细节。"""
    from qadata.llm.gateway import EmbeddingsClient

    batches = []

    class FakeResp:
        def __init__(self, n):
            self.data = [type("D", (), {"embedding": [float(i)]}) for i in range(n)]

    class FakeAPI:
        @staticmethod
        def create(model, input):
            batches.append(list(input))
            return FakeResp(len(input))

    class FakeLimiter:
        def __init__(self):
            self.n = 0

        def acquire(self):
            self.n += 1

    s = Settings(api_key="k", base_url="b", model="m", embed_model="e")
    c = EmbeddingsClient(s)
    c._client = type("C", (), {"embeddings": FakeAPI})()
    lim = FakeLimiter()
    c._limiter = lim
    vecs = c.embed([f"t{i}" for i in range(25)])
    assert [len(b) for b in batches] == [20, 5]
    assert batches[0][0] == "t0" and batches[1][0] == "t20"  # 保序
    assert vecs[0] == [0.0] and vecs[21] == [1.0]  # 跨批拼接＝各批内序复原
    assert lim.n == 2  # 逐 HTTP 请求领限速
    assert c.embed([]) == [] and len(batches) == 2  # 空料零请求


def test_build_llm_precise_mode_uses_precise_temperature(monkeypatch):
    """精准模式（候选>1）温度切换 precise_temperature；默认关闭时仍 0。"""
    captured = {}

    class FakeChatOpenAI:
        def __init__(self, **kw):
            captured.update(kw)

    # M8 票 08：build_llm 现构造 ReasoningChatOpenAI（其 __init__ 即 ChatOpenAI 的，
    # kwargs 透传不变）——钩这个才是真正被构造的那一个。
    monkeypatch.setattr("qadata.llm.gateway.ReasoningChatOpenAI", FakeChatOpenAI)
    build_llm(Settings(api_key="k", base_url="b", model="m",
                       precise_candidates=3, precise_temperature=0.3))
    assert captured["temperature"] == 0.3
    build_llm(Settings(api_key="k", base_url="b", model="m"))
    assert captured["temperature"] == 0


def test_build_llm_passes_timeout(monkeypatch):
    captured = {}

    class FakeChatOpenAI:
        def __init__(self, **kw):
            captured.update(kw)

    monkeypatch.setattr("qadata.llm.gateway.ReasoningChatOpenAI", FakeChatOpenAI)
    build_llm(Settings(api_key="k", base_url="b", model="m", llm_timeout_s=42))
    assert captured["request_timeout"] == 42
    assert captured["stream_usage"] is True  # M8 票 08：流式末 chunk 带 usage（token 聚合来源）
