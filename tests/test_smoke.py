from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mastodon_text_utils import split_mastodon_text


def test_split_mastodon_text_respects_max_len() -> None:
    text = "A" * 1200
    parts = split_mastodon_text(text, max_len=500, sanitize=False)

    assert parts
    assert all(len(part) <= 500 for part in parts)
    assert "".join(parts) == text


def test_split_mastodon_text_sanitizes_mentions() -> None:
    parts = split_mastodon_text("@user meldet https://x.com/foo/bar")
    assert parts == ["#user meldet x/foo/bar"]
