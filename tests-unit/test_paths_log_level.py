import importlib
import json
import logging

from modules import paths_module as paths


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


def test_get_configured_log_level_uses_settings_file_default(monkeypatch, tmp_path) -> None:
    settings_file = tmp_path / "defaults.json"
    settings_file.write_text(json.dumps({"log_level": "ERROR"}), encoding="utf-8")

    monkeypatch.setenv("BOTS_DEFAULT_SETTINGS_FILE", str(settings_file))
    monkeypatch.delenv("BOTS_LOG_LEVEL", raising=False)
    monkeypatch.delenv("LOG_LEVEL", raising=False)

    reloaded = importlib.reload(paths)
    try:
        assert reloaded.get_configured_log_level(default=logging.INFO) == logging.ERROR
    finally:
        monkeypatch.delenv("BOTS_DEFAULT_SETTINGS_FILE", raising=False)
        importlib.reload(paths)
