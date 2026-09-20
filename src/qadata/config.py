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
    llm_timeout_s: float = 120.0  # LLM 客户端超时（M2 实测单次 generate 188s 异常态兜底）
    max_qps: float = 0.0  # 全局限速（次/秒）；0=不限速（并发评测用 --qps 打开）
    precise_candidates: int = 1  # M4-C 精准模式候选数（1=关闭；3/5 建议奇数）
    precise_temperature: float = 0.3  # 精准模式采样温度（候选>1 时生效；关闭时无效）
    metric_layer: bool = False  # M5 票 05 指标层总开关（False 时管线与纯 SQL 现状逐行为一致）
    metrics_dir: str = "metrics"  # 注册表目录（按库名寻址 metrics/<db>.yaml）
    value_sampling: bool = False  # M8 票 02 值采样注入（False 时 schema 上下文与现状逐字节一致）
    clarification: bool = False  # M8 票 03 澄清回合（False 时 understand prompt 与路由 map 与今日逐字节一致）
    otel_enabled: bool = False  # M9 票 01 OTLP 上报出口（False＝noop，埋点帧流零挂接、行为逐字节照旧）
    otel_endpoint: str = ""     # OTLP HTTP 收集端点（Langfuse 等；空＝回落 OTEL_EXPORTER_OTLP_ENDPOINT）


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


_TRUE_FORMS = {"1", "true", "yes"}
_FALSE_FORMS = {"0", "false", "no"}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    if raw in _TRUE_FORMS:
        return True
    if raw in _FALSE_FORMS:
        return False
    raise RuntimeError(f"环境变量 {name} 需布尔形态（1/0/true/false/yes/no），当前值：{raw!r}")


def load_settings() -> Settings:
    # M9 票 04：tiktoken 词表＝仓库硬资产——启动即查、缺失如实炸（无网络兜底；
    # 惰性 import 防 config→gssc→prompts→precise→executor→config 顶层成环）
    from qadata.graph.gssc import check_vocab
    check_vocab()
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
        llm_timeout_s=_env_float("QADATA_LLM_TIMEOUT_S", 120.0, minimum=10),
        max_qps=_env_float("QADATA_MAX_QPS", 0.0, minimum=0.0),
        precise_candidates=_env_int("QADATA_PRECISE_CANDIDATES", 1, minimum=1),
        precise_temperature=_env_float("QADATA_PRECISE_TEMPERATURE", 0.3, minimum=0.0),
        metric_layer=_env_bool("QADATA_METRIC_LAYER", False),
        metrics_dir=os.getenv("QADATA_METRICS_DIR", "metrics"),
        value_sampling=_env_bool("QADATA_VALUE_SAMPLING", False),
        clarification=_env_bool("QADATA_CLARIFICATION", False),
        otel_enabled=_env_bool("QADATA_OTEL_ENABLED", False),
        otel_endpoint=os.getenv("QADATA_OTEL_ENDPOINT", ""),
    )


# 无密钥场景的默认值实例（假模型测试路径；生产路径一律走 load_settings）
FALLBACK_SETTINGS = Settings(api_key="", base_url="", model="")
