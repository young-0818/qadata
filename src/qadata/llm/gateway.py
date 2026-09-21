"""模型网关：OpenAI 兼容协议统一接入（默认 DeepSeek）＋指数退避。"""
import random
import time

import openai
from langchain_core.messages import AIMessageChunk
from langchain_openai import ChatOpenAI

from qadata.config import Settings, load_settings

RETRYABLE_STATUS = {429, 500, 502, 503, 504}  # 限流与服务端抖动；401/403 不重试

_EMBED_BATCH_MAX = 20  # /v1/embeddings 单请求条数上限（百炼实拍 400「should not be
                       # larger than 20」，M10 票 07 合成库建卡撞出；换端点若更高改此数）


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


class EmbeddingsClient:
    """M9 票 06 例题库召回的向量化通道：OpenAI 兼容 /v1/embeddings（与正文模型同端点）。

    协议面只有 `embed(texts) -> 向量组`——召回账本记的是「一次向量化调用」本体
    （spec §二 Q6 花费如实入账），端点 usage 不经此协议进账。限速器自持一把
    （QADATA_MAX_QPS 语义＝只限这条向量线——eval 结构上到不了 embedder，web 正文
    模型调用本也不经 limiter，双轴评审如实注）。不做退避重试：召回失败＝降级不召回
    （票 06 失败纪律），重试属锦上添花的第二次花费，不值。"""

    def __init__(self, settings: Settings, limiter=None):
        self.model = settings.embed_model
        self._limiter = limiter
        self._client = openai.OpenAI(api_key=settings.api_key,
                                     base_url=settings.base_url,
                                     timeout=settings.llm_timeout_s)

    def embed(self, texts: list[str]) -> list[list[float]]:
        # 端点批上限 ≤20（百炼 qwen3.7-text-embedding 实拍 400，票 07 合成库建卡撞出）
        # ——切批在唯一协议面收口，调用方「一次向量化调用＝档面本体」记法不变
        # （HTTP 批数是实现细节，账本入的是逻辑调用）。限速逐 HTTP 请求照领。
        out: list[list[float]] = []
        for i in range(0, len(texts), _EMBED_BATCH_MAX):
            if self._limiter is not None:
                self._limiter.acquire()
            resp = self._client.embeddings.create(model=self.model,
                                                  input=texts[i:i + _EMBED_BATCH_MAX])
            out.extend(d.embedding for d in resp.data)
        return out


def build_embedder(settings: Settings | None = None) -> EmbeddingsClient:
    """生产位构造（serve 只在 settings.embed_model 非空时调用——空＝召回未配置）。"""
    from qadata.llm.ratelimit import (
        RateLimiter,  # 惰性 import 防环（config::check_vocab 先例）
    )
    s = settings or load_settings()
    limiter = RateLimiter(s.max_qps) if s.max_qps > 0 else None
    return EmbeddingsClient(s, limiter=limiter)


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
