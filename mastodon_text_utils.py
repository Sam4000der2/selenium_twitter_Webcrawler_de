MASTODON_DEFAULT_MAX = 500


def sanitize_for_mastodon(text: str) -> str:
    text = text.replace('@', '#')
    while '##' in text:
        text = text.replace('##', '#')
    text = text.replace('https://x.com', 'x')
    return text


def split_mastodon_text(text: str, max_len: int = MASTODON_DEFAULT_MAX, sanitize: bool = True) -> list[str]:
    """
    Split a Mastodon status into chunks under the character limit.
    Tries to break on separators before falling back to a hard cut.
    """
    cleaned = sanitize_for_mastodon(text.strip()) if sanitize else text.strip()
    parts: list[str] = []
    remaining = cleaned

    while remaining:
        if len(remaining) <= max_len:
            parts.append(remaining)
            break

        chunk = remaining[:max_len]
        split_at = max_len
        for sep in ["\n\n", "\n", ". ", ", ", " "]:
            idx = chunk.rfind(sep)
            if idx > 0:
                split_at = idx + len(sep)
                break

        next_part = remaining[:split_at].rstrip()
        parts.append(next_part)
        remaining = remaining[split_at:].lstrip()

    return parts
