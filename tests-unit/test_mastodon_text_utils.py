from mastodon_text_utils import sanitize_for_mastodon, split_mastodon_text


def test_sanitize_for_mastodon_rewrites_mentions_and_x_links() -> None:
    text = "@sbahn https://x.com/SBahnBerlin/status/123"
    result = sanitize_for_mastodon(text)

    assert result.startswith("#sbahn")
    assert "https://x.com" not in result
    assert "x/SBahnBerlin/status/123" in result


def test_split_mastodon_text_respects_max_len() -> None:
    text = " ".join(["segment"] * 120)
    parts = split_mastodon_text(text, max_len=120, sanitize=False)

    assert len(parts) > 1
    assert all(parts)
    assert all(len(part) <= 120 for part in parts)


def test_split_mastodon_text_honors_first_min_len() -> None:
    text = "Header: " + ("abc " * 80)
    parts = split_mastodon_text(text, max_len=100, sanitize=False, min_len=20, first_min_len=60)

    assert len(parts) > 1
    assert len(parts[0]) >= 60
    assert all(len(part) <= 100 for part in parts)
