MASTODON_DEFAULT_MAX = 500


def sanitize_for_mastodon(text: str) -> str:
    text = text.replace('@', '#')
    while '##' in text:
        text = text.replace('##', '#')
    text = text.replace('https://x.com', 'x')
    return text


def split_mastodon_text(
    text: str,
    max_len: int = MASTODON_DEFAULT_MAX,
    sanitize: bool = True,
    min_len: int = 0,
    first_min_len: int = 0,
) -> list[str]:
    """
    Split a Mastodon status into chunks under the character limit.
    Tries to break on separators while keeping each part above the given
    minimum length when possible. The first part can enforce a higher minimum
    length (e.g., to keep a header and enough content together).
    """
    cleaned = sanitize_for_mastodon(text.strip()) if sanitize else text.strip()
    max_len = max(1, max_len)
    min_len = max(0, min(min_len, max_len))
    first_min_len = max(0, min(first_min_len, max_len))

    parts: list[str] = []
    remaining = cleaned
    first_pass = True

    def pick_split(buffer: str, min_required: int) -> int:
        def is_valid(split_at: int) -> bool:
            if split_at <= 0:
                return False

            chunk_len = split_at
            remaining_len = len(remaining) - split_at

            if chunk_len < min_required:
                return False

            if remaining_len and remaining_len < min_len and remaining_len <= max_len:
                # Avoid leaving a dangling short remainder when this could be
                # the final post.
                return False

            return True

        for sep in ["\n\n", "\n", ". ", ", ", " "]:
            idx = buffer.rfind(sep)
            if idx > 0:
                candidate = idx + len(sep)
                if is_valid(candidate):
                    return candidate

        remaining_after_max = len(remaining) - max_len
        if remaining_after_max < min_required <= max_len:
            adjusted = len(remaining) - min_required
            if adjusted > 0:
                return min(max_len, max(min_required, adjusted))

        return max_len

    while remaining:
        if len(remaining) <= max_len:
            parts.append(remaining)
            break

        window = remaining[:max_len]
        current_min = max(min_len, first_min_len) if first_pass else min_len
        split_at = pick_split(window, current_min)
        split_at = max(1, split_at)

        next_part = remaining[:split_at].rstrip()
        if not next_part:
            next_part = remaining[:max_len].strip()

        parts.append(next_part)
        remaining = remaining[split_at:].lstrip()
        first_pass = False

    return parts
