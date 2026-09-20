import pytest

from qadata.config import Settings, load_settings


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """隔离：忽略 .env 文件并清空相关 shell 变量，测试只认显式设置的值。

    （load_settings 会调 load_dotenv() 读取仓库根目录 .env——不隔离的话，
    按 README 建好 .env 后本测试文件会莫名失败。）
    """
    monkeypatch.setattr("qadata.config.load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("QADATA_BASE_URL", raising=False)
    monkeypatch.delenv("QADATA_MODEL", raising=False)
    monkeypatch.delenv("QADATA_MAX_ROWS", raising=False)
    monkeypatch.delenv("QADATA_RETRY_BUDGET", raising=False)
    monkeypatch.delenv("QADATA_SQL_TIMEOUT_S", raising=False)
    monkeypatch.delenv("QADATA_LLM_TIMEOUT_S", raising=False)
    monkeypatch.delenv("QADATA_MAX_QPS", raising=False)
    monkeypatch.delenv("QADATA_PRECISE_CANDIDATES", raising=False)
    monkeypatch.delenv("QADATA_PRECISE_TEMPERATURE", raising=False)
    monkeypatch.delenv("QADATA_METRIC_LAYER", raising=False)
    monkeypatch.delenv("QADATA_METRICS_DIR", raising=False)
    monkeypatch.delenv("QADATA_VALUE_SAMPLING", raising=False)
    monkeypatch.delenv("QADATA_CLARIFICATION", raising=False)
    monkeypatch.delenv("QADATA_OTEL_ENABLED", raising=False)
    monkeypatch.delenv("QADATA_OTEL_ENDPOINT", raising=False)
    monkeypatch.delenv("QADATA_EMBED_MODEL", raising=False)


def test_load_settings_reads_env(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    s = load_settings()
    assert isinstance(s, Settings)
    assert s.api_key == "sk-test"
    assert s.base_url == "https://api.deepseek.com"  # 默认值
    assert s.model == "deepseek-chat"
    assert s.max_rows == 50


def test_load_settings_missing_key():
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        load_settings()


def test_load_settings_new_defaults(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    s = load_settings()
    assert s.retry_budget == 3 and s.sql_timeout_s == 5.0 and s.max_rows == 50


def test_bad_max_rows_readable_error(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("QADATA_MAX_ROWS", "abc")
    with pytest.raises(RuntimeError, match="QADATA_MAX_ROWS"):
        load_settings()


def test_bad_retry_budget_readable_error(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("QADATA_RETRY_BUDGET", "-1")
    with pytest.raises(RuntimeError, match="QADATA_RETRY_BUDGET"):
        load_settings()


def test_bad_sql_timeout_readable_error(monkeypatch):
    """终审建议：浮点坏值也走可读契约（与 _env_int 一致，含变量名）。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("QADATA_SQL_TIMEOUT_S", "abc")
    with pytest.raises(RuntimeError, match="QADATA_SQL_TIMEOUT_S"):
        load_settings()


def test_zero_sql_timeout_rejected(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("QADATA_SQL_TIMEOUT_S", "0")
    with pytest.raises(RuntimeError, match="QADATA_SQL_TIMEOUT_S"):
        load_settings()


def test_load_settings_llm_timeout_default(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    assert load_settings().llm_timeout_s == 120


def test_bad_llm_timeout_readable_error(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("QADATA_LLM_TIMEOUT_S", "abc")
    with pytest.raises(RuntimeError, match="QADATA_LLM_TIMEOUT_S"):
        load_settings()


def test_load_settings_max_qps_default(monkeypatch):
    """默认不限速（0）；并发评测按需用 --qps 打开。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    assert load_settings().max_qps == 0.0


def test_load_settings_max_qps_from_env(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("QADATA_MAX_QPS", "8")
    assert load_settings().max_qps == 8.0


def test_bad_max_qps_readable_error(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("QADATA_MAX_QPS", "abc")
    with pytest.raises(RuntimeError, match="QADATA_MAX_QPS"):
        load_settings()


def test_precise_defaults_off(monkeypatch):
    """精准模式默认关闭：候选数 1（不投票），温度 0.3 仅精准态使用。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    s = load_settings()
    assert s.precise_candidates == 1 and s.precise_temperature == 0.3


def test_precise_candidates_from_env(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("QADATA_PRECISE_CANDIDATES", "3")
    assert load_settings().precise_candidates == 3


def test_precise_candidates_below_one_rejected(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("QADATA_PRECISE_CANDIDATES", "0")
    with pytest.raises(RuntimeError, match="QADATA_PRECISE_CANDIDATES"):
        load_settings()


def test_negative_precise_temperature_rejected(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("QADATA_PRECISE_TEMPERATURE", "-1")
    with pytest.raises(RuntimeError, match="QADATA_PRECISE_TEMPERATURE"):
        load_settings()


def test_metric_layer_defaults_off(monkeypatch):
    """指标层总开关默认关（负结果预案⑤：功能保留、主线数字零污染）。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    s = load_settings()
    assert s.metric_layer is False and s.metrics_dir == "metrics"


def test_metric_layer_env_on(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("QADATA_METRIC_LAYER", "1")
    assert load_settings().metric_layer is True


@pytest.mark.parametrize("raw", ["0", "false", "False", "no"])
def test_metric_layer_env_off_forms(monkeypatch, raw):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("QADATA_METRIC_LAYER", raw)
    assert load_settings().metric_layer is False


def test_bad_metric_layer_rejected(monkeypatch):
    """布尔形态坏值走可读契约（与 _env_int/_env_float 同族纪律）。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("QADATA_METRIC_LAYER", "maybe")
    with pytest.raises(RuntimeError, match="QADATA_METRIC_LAYER"):
        load_settings()


def test_value_sampling_defaults_off(monkeypatch):
    """M8 票 02：值采样默认关（关态 schema 上下文逐字节一致是专测面）。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    assert load_settings().value_sampling is False


def test_value_sampling_env_on(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("QADATA_VALUE_SAMPLING", "1")
    assert load_settings().value_sampling is True


def test_bad_value_sampling_rejected(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("QADATA_VALUE_SAMPLING", "maybe")
    with pytest.raises(RuntimeError, match="QADATA_VALUE_SAMPLING"):
        load_settings()


def test_clarification_defaults_off(monkeypatch):
    """M8 票 03：澄清回合默认关（关态 prompt 与路由 map 与今日一致是专测面）。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    assert load_settings().clarification is False


def test_clarification_env_on(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("QADATA_CLARIFICATION", "1")
    assert load_settings().clarification is True


def test_bad_clarification_rejected(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("QADATA_CLARIFICATION", "maybe")
    with pytest.raises(RuntimeError, match="QADATA_CLARIFICATION"):
        load_settings()


def test_metrics_dir_from_env(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("QADATA_METRICS_DIR", "other_metrics")
    assert load_settings().metrics_dir == "other_metrics"


def test_otel_defaults_off(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    s = load_settings()
    assert s.otel_enabled is False and s.otel_endpoint == ""


def test_otel_env_on(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("QADATA_OTEL_ENABLED", "1")
    monkeypatch.setenv("QADATA_OTEL_ENDPOINT", "http://localhost:3000/api/public/otel")
    s = load_settings()
    assert s.otel_enabled is True
    assert s.otel_endpoint == "http://localhost:3000/api/public/otel"


def test_bad_otel_enabled_rejected(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("QADATA_OTEL_ENABLED", "maybe")
    with pytest.raises(RuntimeError, match="QADATA_OTEL_ENABLED"):
        load_settings()


def test_embed_model_defaults_empty(monkeypatch):
    """M9 票 06：向量化模型默认空＝召回未配置（例题库有货也如实入账不召回；
    真启用＝签题日 owner 显式配置，见票 06 判据）。"""
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    assert load_settings().embed_model == ""


def test_embed_model_from_env(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("QADATA_EMBED_MODEL", "text-embedding-v3")
    assert load_settings().embed_model == "text-embedding-v3"
