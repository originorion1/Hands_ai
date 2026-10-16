"""Match fixed API routes without treating marketing tags as routes."""

from urllib.parse import parse_qsl, urlsplit

_TRACKING_KEYS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
})


def is_api_target(raw_target: str, expected_path: str) -> bool:
    try:
        target = urlsplit(raw_target)
    except ValueError:
        return False
    if target.path != expected_path or target.fragment:
        return False
    if not target.query:
        return True
    if len(target.query) > 512:
        return False
    try:
        pairs = parse_qsl(target.query, strict_parsing=True, max_num_fields=5)
    except ValueError:
        return False
    return (
        bool(pairs)
        and len({key for key, _ in pairs}) == len(pairs)
        and all(key in _TRACKING_KEYS and 0 < len(value) <= 128 for key, value in pairs)
    )
