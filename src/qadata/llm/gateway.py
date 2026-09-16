"""模型网关：OpenAI 兼容协议统一接入（默认 DeepSeek）＋指数退避。"""
import random
import time

import openai
from langchain_core.messages import AIMessageChunk
from langchain_openai import ChatOpenAI

from qadata.config import Settings, load_settings

RETRYABLE_STATUS = {429, 500, 502, 503, 504}  # 限流与服务端抖动；401/403 不重试


class ReasoningChatOpenAI(ChatOpenAI):
    """把百炼兼容端流式 delta 里的 `reasoning_content` 提进 chunk.additional_kwargs。

    langchain-openai 1.6 的 Chat Completions 路径明确**不提取**非标准字段（其 docstring
    直接建议「用 provider-specific subclass」），探针实拍 `llm.stream` 的 chunk 里
    additional_kwargs 恒空——思考流无处可取。覆写这一个转换钩子即可，`invoke` 路径
    根本不走它（逐行为不变，M8 票 08 关态姊妹钉的前提）。"""

    def _convert_chunk_to_generation_chunk(self, chunk, default_chunk_class,
                                           base_generation_info):
        gc = super()._convert_chunk_to_generation_chunk(chunk, default_chunk_class,
                                                        base_generation_info)
        if gc is not None and isinstance(gc.message, AIMessageChunk):
            choices = chunk.get("choices") or []
            rc = (choices[0].get("delta") or {}).get("reasoning_content") if choices else None
            if rc:
                gc.message.additional_kwargs["reasoning_content"] = rc
        return gc


def build_llm(settings: Settings | None = None) -> ChatOpenAI:
    s = settings or load_settings()
    # 精准模式（候选>1）用 precise_temperature 制造候选多样性；关闭时保持 temperature=0
    temp = s.precise_temperature if s.precise_candidates > 1 else 0
    return ReasoningChatOpenAI(
        model=s.model,
        api_key=s.api_key,
        base_url=s.base_url,
        temperature=temp,
        request_timeout=s.llm_timeout_s,
        stream_usage=True,  # M8 票 08：流式末 chunk 带 usage_metadata（token 聚合进账本的来源）
    )


def _is_retryable(exc: Exception) -> bool:
    """只有"再试一次可能成功"的错误才重试；鉴权/参数错立即失败。"""
    if isinstance(exc, openai.APIConnectionError):  # 含 APITimeoutError 子类
        return True
    if isinstance(exc, openai.APIStatusError) and exc.status_code in RETRYABLE_STATUS:
        return True
    return isinstance(exc, (ConnectionError, TimeoutError))


def backoff_delay(attempt: int, base: float = 1.0) -> float:
    """第 attempt（0 起）次重试的等待秒数：指数退避＋[0.5,1.0) 倍乘抖动（防雪崩）。
    invoke_with_backoff 与 timed_stream（M8 票 08）共用同一支退避曲线——两处曲线
    字面漂移是 M4 端点抖动教训的对偶（tool_frame／compose_supplement 单源先例）。"""
    return base * (2 ** attempt) * (0.5 + random.random() / 2)


def invoke_with_backoff(llm, prompt: str, *, max_retries: int = 3,
                        base_delay: float = 1.0, sleep=time.sleep, limiter=None):
    """指数退避＋抖动（backoff_delay）；不可重试或耗尽即上抛。
    limiter：可选 RateLimiter——每次真实调用（含重试）前 acquire（并发评测限速）。"""
    for attempt in range(max_retries + 1):
        try:
            if limiter is not None:
                limiter.acquire()
            return llm.invoke(prompt)
        except Exception as e:
            if attempt >= max_retries or not _is_retryable(e):
                raise
            sleep(backoff_delay(attempt, base_delay))
