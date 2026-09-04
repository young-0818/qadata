"""模型网关：OpenAI 兼容协议统一接入（默认 DeepSeek）＋指数退避。"""
import random
import time

import openai
from langchain_openai import ChatOpenAI

from qadata.config import Settings, load_settings

RETRYABLE_STATUS = {429, 500, 502, 503, 504}  # 限流与服务端抖动；401/403 不重试


def build_llm(settings: Settings | None = None) -> ChatOpenAI:
    s = settings or load_settings()
    return ChatOpenAI(
        model=s.model,
        api_key=s.api_key,
        base_url=s.base_url,
        temperature=0,
        request_timeout=s.llm_timeout_s,
    )


def _is_retryable(exc: Exception) -> bool:
    """只有"再试一次可能成功"的错误才重试；鉴权/参数错立即失败。"""
    if isinstance(exc, openai.APIConnectionError):  # 含 APITimeoutError 子类
        return True
    if isinstance(exc, openai.APIStatusError) and exc.status_code in RETRYABLE_STATUS:
        return True
    return isinstance(exc, (ConnectionError, TimeoutError))


def invoke_with_backoff(llm, prompt: str, *, max_retries: int = 3,
                        base_delay: float = 1.0, sleep=time.sleep):
    """指数退避＋抖动（[0.5,1.0) 倍乘避免雪崩）；不可重试或耗尽即上抛。"""
    for attempt in range(max_retries + 1):
        try:
            return llm.invoke(prompt)
        except Exception as e:
            if attempt >= max_retries or not _is_retryable(e):
                raise
            sleep(base_delay * (2 ** attempt) * (0.5 + random.random() / 2))
