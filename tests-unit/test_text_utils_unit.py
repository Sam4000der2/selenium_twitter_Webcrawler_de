from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mastodon_text_utils import sanitize_for_mastodon, split_mastodon_text


def test_sanitize_for_mastodon_rewrites_mentions_and_x_urls() -> None:
    text = "@alice meldet https://x.com/SBahnBerlin/status/123"
    assert sanitize_for_mastodon(text) == "#alice meldet x/SBahnBerlin/status/123"


def test_split_mastodon_text_respects_min_lengths() -> None:
    text = "A" * 90 + " " + "B" * 90 + " " + "C" * 90
    parts = split_mastodon_text(
        text,
        max_len=110,
        sanitize=False,
        min_len=60,
        first_min_len=80,
    )

    assert len(parts) == 3
    assert len(parts[0]) >= 80
    assert all(len(part) >= 60 for part in parts[:-1])
    assert "".join(parts).replace(" ", "") == text.replace(" ", "")
