from langchain_openai import ChatOpenAI

from qadata.config import Settings
from qadata.llm.gateway import build_llm


def test_build_llm_uses_settings():
    s = Settings(api_key="sk-test", base_url="https://api.deepseek.com", model="deepseek-chat")
    llm = build_llm(s)
    assert isinstance(llm, ChatOpenAI)
    assert llm.temperature == 0
    assert llm.openai_api_base == "https://api.deepseek.com"
