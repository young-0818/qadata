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
