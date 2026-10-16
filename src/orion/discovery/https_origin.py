"""Canonical HTTPS origin validation for governed source configurations."""

from urllib.parse import urlsplit


def _normalize_base_url(base_url: str) -> str:
    if not isinstance(base_url, str) or not base_url:
        raise ValueError("base_url must be non-empty")
    if base_url != base_url.strip():
        raise ValueError("base_url must not contain surrounding whitespace")

    parsed = urlsplit(base_url)

    if parsed.scheme.lower() != "https":
        raise ValueError("base_url must use HTTPS")
    if not parsed.hostname:
        raise ValueError("base_url must include a hostname")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("base_url must not contain credentials")
    if parsed.path not in {"", "/"}:
        raise ValueError("base_url must contain only the HTTPS origin")
    if parsed.query or parsed.fragment:
        raise ValueError("base_url must not contain query parameters or fragments")
    if any(character.isspace() for character in parsed.netloc):
        raise ValueError("base_url hostname must not contain whitespace")

    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("base_url contains an invalid port") from exc

    return f"https://{parsed.netloc}"
