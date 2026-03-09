import asyncio

from bots import mastodon_control_bot as bot


def _run(coro):
    asyncio.run(coro)


def test_handle_command_ignores_non_command_mentions(monkeypatch) -> None:
    sent: list[dict] = []

    def fake_send_dm(_mastodon, acct: str, in_reply_to_id, text: str, include_tagging_hint: bool = True):
        sent.append(
            {
                "acct": acct,
                "in_reply_to_id": in_reply_to_id,
                "text": text,
                "include_tagging_hint": include_tagging_hint,
            }
        )

    monkeypatch.setattr(bot, "send_dm", fake_send_dm)
    bot.USER_STATES.clear()

    status = {"id": 1001, "content": "<p>@controlbot Danke fuer den Hinweis</p>"}
    account = {"acct": "alice"}

    _run(bot.handle_command(object(), "opnv_berlin", status, account))

    assert sent == []


def test_handle_command_processes_explicit_slash_command(monkeypatch) -> None:
    sent: list[dict] = []

    def fake_send_dm(_mastodon, acct: str, in_reply_to_id, text: str, include_tagging_hint: bool = True):
        sent.append(
            {
                "acct": acct,
                "in_reply_to_id": in_reply_to_id,
                "text": text,
                "include_tagging_hint": include_tagging_hint,
            }
        )

    monkeypatch.setattr(bot, "send_dm", fake_send_dm)
    monkeypatch.setattr(bot, "build_status_text", lambda: "STATUS_OK")
    bot.USER_STATES.clear()

    status = {"id": 1002, "content": "<p>@controlbot /status</p>"}
    account = {"acct": "alice"}

    _run(bot.handle_command(object(), "opnv_berlin", status, account))

    assert len(sent) == 1
    assert sent[0]["text"] == "STATUS_OK"


def test_handle_command_accepts_slash_command_with_trailing_parenthesis(monkeypatch) -> None:
    sent: list[dict] = []

    def fake_send_dm(_mastodon, acct: str, in_reply_to_id, text: str, include_tagging_hint: bool = True):
        sent.append(
            {
                "acct": acct,
                "in_reply_to_id": in_reply_to_id,
                "text": text,
                "include_tagging_hint": include_tagging_hint,
            }
        )

    monkeypatch.setattr(bot, "send_dm", fake_send_dm)
    monkeypatch.setattr(bot, "help_text", lambda: "HELP_OK")
    bot.USER_STATES.clear()

    status = {"id": 1005, "content": "<p>@controlbot /help)</p>"}
    account = {"acct": "alice"}

    _run(bot.handle_command(object(), "opnv_berlin", status, account))

    assert len(sent) == 1
    assert sent[0]["text"] == "HELP_OK"


def test_handle_command_replies_for_unknown_explicit_slash_command(monkeypatch) -> None:
    sent: list[dict] = []

    def fake_send_dm(_mastodon, acct: str, in_reply_to_id, text: str, include_tagging_hint: bool = True):
        sent.append(
            {
                "acct": acct,
                "in_reply_to_id": in_reply_to_id,
                "text": text,
                "include_tagging_hint": include_tagging_hint,
            }
        )

    monkeypatch.setattr(bot, "send_dm", fake_send_dm)
    bot.USER_STATES.clear()

    status = {"id": 1003, "content": "<p>@controlbot /foobar</p>"}
    account = {"acct": "alice"}

    _run(bot.handle_command(object(), "opnv_berlin", status, account))

    assert len(sent) == 1
    assert "Das habe ich nicht verstanden." in sent[0]["text"]


def test_handle_command_keeps_pending_dialog_without_slash(monkeypatch) -> None:
    sent: list[dict] = []
    pending_inputs: list[str] = []

    def fake_send_dm(_mastodon, acct: str, in_reply_to_id, text: str, include_tagging_hint: bool = True):
        sent.append(
            {
                "acct": acct,
                "in_reply_to_id": in_reply_to_id,
                "text": text,
                "include_tagging_hint": include_tagging_hint,
            }
        )

    async def fake_handle_pending_state(_mastodon, _instance_name, _acct, _status_id, text):
        pending_inputs.append(text)
        return True

    monkeypatch.setattr(bot, "send_dm", fake_send_dm)
    monkeypatch.setattr(bot, "handle_pending_state", fake_handle_pending_state)
    bot.USER_STATES.clear()

    status = {"id": 1004, "content": "<p>@controlbot ja</p>"}
    account = {"acct": "alice"}

    _run(bot.handle_command(object(), "opnv_berlin", status, account))

    assert pending_inputs == ["ja"]
    assert sent == []
