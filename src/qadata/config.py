"""全局配置：从环境变量/.env 读取。"""
import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass
class Settings:
    api_key: str
    base_url: str
    model: str
    max_rows: int = 50        # 显示上限：喂给 respond/CLI 的行数（M2 接线）
    retry_budget: int = 3     # 自纠错总尝试次数（含首次）
    sql_timeout_s: float = 5.0  # 沙箱资源层：查询超时


def _env_int(name: str, default: int, *, minimum: int | None = None) -> int:
    raw = os.getenv(name, str(default))
    try:
        v = int(raw)
    except ValueError:
        raise RuntimeError(f"环境变量 {name} 需要整数，当前值：{raw!r}") from None
    if minimum is not None and v < minimum:
        raise RuntimeError(f"环境变量 {name} 需 ≥{minimum}，当前值：{v}")
    return v


def _env_float(name: str, default: float, *, minimum: float | None = None) -> float:
    raw = os.getenv(name, str(default))
    try:
        v = float(raw)
    except ValueError:
        raise RuntimeError(f"环境变量 {name} 需要数字，当前值：{raw!r}") from None
    if minimum is not None and v < minimum:
        raise RuntimeError(f"环境变量 {name} 需 ≥{minimum}（设 0 会让一切查询超时），当前值：{v}")
    return v


def load_settings() -> Settings:
    load_dotenv()
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("缺少 DEEPSEEK_API_KEY（复制 .env.example 为 .env 并填写）")
    return Settings(
        api_key=api_key,
        base_url=os.getenv("QADATA_BASE_URL", "https://api.deepseek.com"),
        model=os.getenv("QADATA_MODEL", "deepseek-chat"),
        max_rows=_env_int("QADATA_MAX_ROWS", 50, minimum=1),
        retry_budget=_env_int("QADATA_RETRY_BUDGET", 3, minimum=1),
        sql_timeout_s=_env_float("QADATA_SQL_TIMEOUT_S", 5.0, minimum=0.1),
    )


# 无密钥场景的默认值实例（假模型测试路径；生产路径一律走 load_settings）
FALLBACK_SETTINGS = Settings(api_key="", base_url="", model="")
