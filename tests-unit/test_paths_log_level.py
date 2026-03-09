import logging

import paths


def test_parse_log_level_supports_named_levels() -> None:
    assert paths.parse_log_level("debug", default=logging.INFO) == logging.DEBUG
    assert paths.parse_log_level("WARNING", default=logging.INFO) == logging.WARNING


def test_parse_log_level_supports_numeric_levels() -> None:
    assert paths.parse_log_level("20", default=logging.WARNING) == 20


def test_parse_log_level_falls_back_to_default_for_invalid() -> None:
    assert paths.parse_log_level("not-a-level", default=logging.ERROR) == logging.ERROR
    assert paths.parse_log_level("", default=logging.ERROR) == logging.ERROR


def test_get_configured_log_level_prefers_bots_log_level(monkeypatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "ERROR")
    monkeypatch.setenv("BOTS_LOG_LEVEL", "DEBUG")
    assert paths.get_configured_log_level(default=logging.INFO) == logging.DEBUG


def test_get_configured_log_level_uses_log_level_fallback(monkeypatch) -> None:
    monkeypatch.delenv("BOTS_LOG_LEVEL", raising=False)
    monkeypatch.setenv("LOG_LEVEL", "WARNING")
    assert paths.get_configured_log_level(default=logging.INFO) == logging.WARNING
