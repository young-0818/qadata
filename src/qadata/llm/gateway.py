"""模型网关：OpenAI 兼容协议统一接入（默认 DeepSeek）。"""
from langchain_openai import ChatOpenAI

from qadata.config import Settings, load_settings


def build_llm(settings: Settings | None = None) -> ChatOpenAI:
    s = settings or load_settings()
    return ChatOpenAI(
        model=s.model,
        api_key=s.api_key,
        base_url=s.base_url,
        temperature=0,
    )
