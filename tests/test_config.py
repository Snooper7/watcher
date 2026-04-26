import logging
import os
import pytest

logger = logging.getLogger(__name__)


def test_config_loads_successfully(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123456:ABC-test-token")
    monkeypatch.setenv("GROUP_CHAT_ID", "-100123456789")

    from bot.config import Settings
    settings = Settings()
    logger.debug("[test_config_loads_successfully] Settings loaded: %s", settings.LOG_LEVEL)

    assert settings.BOT_TOKEN == "123456:ABC-test-token"
    assert settings.GROUP_CHAT_ID == "-100123456789"


def test_config_missing_bot_token(monkeypatch):
    monkeypatch.delenv("BOT_TOKEN", raising=False)
    monkeypatch.setenv("GROUP_CHAT_ID", "-100123456789")

    from bot.config import Settings
    with pytest.raises(ValueError, match="BOT_TOKEN"):
        Settings()
    logger.debug("[test_config_missing_bot_token] ValueError raised as expected")


def test_config_missing_group_chat_id(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123456:ABC-test-token")
    monkeypatch.delenv("GROUP_CHAT_ID", raising=False)

    from bot.config import Settings
    with pytest.raises(ValueError, match="GROUP_CHAT_ID"):
        Settings()
    logger.debug("[test_config_missing_group_chat_id] ValueError raised as expected")


def test_config_default_values(monkeypatch):
    monkeypatch.setenv("BOT_TOKEN", "123456:ABC-test-token")
    monkeypatch.setenv("GROUP_CHAT_ID", "-100123456789")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    monkeypatch.delenv("CHECK_TIMES", raising=False)

    from bot.config import Settings
    settings = Settings()
    logger.debug("[test_config_default_values] DATABASE_URL=%s LOG_LEVEL=%s", settings.DATABASE_URL, settings.LOG_LEVEL)

    assert settings.DATABASE_URL == "sqlite:///data/whatcher.db"
    assert settings.LOG_LEVEL == "DEBUG"
    assert settings.CHECK_TIMES == ["7:00", "13:00", "20:00"]
