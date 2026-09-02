"""全局配置：从环境变量/.env 读取。"""
import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass
class Settings:
    api_key: str
    base_url: str
    model: str
    max_rows: int = 50


def load_settings() -> Settings:
    load_dotenv()
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("缺少 DEEPSEEK_API_KEY（复制 .env.example 为 .env 并填写）")
    return Settings(
        api_key=api_key,
        base_url=os.getenv("QADATA_BASE_URL", "https://api.deepseek.com"),
        model=os.getenv("QADATA_MODEL", "deepseek-chat"),
        max_rows=int(os.getenv("QADATA_MAX_ROWS", "50")),
    )
