from modules import control_bot_utils_module as utils


def test_split_log_level_and_body_supports_colon_format() -> None:
    level, body = utils.split_log_level_and_body("INFO:telegram_bot: Nachricht gesendet")
    assert level == "INFO"
    assert body == "telegram_bot: Nachricht gesendet"


def test_split_log_level_and_body_supports_whitespace_format() -> None:
    level, body = utils.split_log_level_and_body("WARNING nitter_bot: Feedparser bozo")
    assert level == "WARNING"
    assert body == "nitter_bot: Feedparser bozo"


def test_split_log_level_and_body_handles_lowercase_and_empty_body() -> None:
    level, body = utils.split_log_level_and_body("error")
    assert level == "ERROR"
    assert body == ""


def test_split_log_level_and_body_returns_none_for_unknown_prefix() -> None:
    level, body = utils.split_log_level_and_body("telegram_bot: plain text line")
    assert level is None
    assert body == "telegram_bot: plain text line"
