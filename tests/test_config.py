import pytest
from qadata.config import Settings, load_settings


def test_load_settings_reads_env(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.delenv("QADATA_BASE_URL", raising=False)
    monkeypatch.delenv("QADATA_MODEL", raising=False)
    s = load_settings()
    assert isinstance(s, Settings)
    assert s.api_key == "sk-test"
    assert s.base_url == "https://api.deepseek.com"  # 默认值
    assert s.model == "deepseek-chat"
    assert s.max_rows == 50


def test_load_settings_missing_key(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        load_settings()
