"""ERPNext/Frappe read-only adapter for the first ORION prototype.

ERPNext is a starting customer environment, not an ORION architectural
dependency. This adapter translates observations into ORION's neutral
contracts; no ERPNext semantics leak into the Kernel.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from ..contracts import Evidence, EvidenceKind, Observation, ObservationMode

DEFAULT_TIMEOUT_SECONDS = 20
DEFAULT_PAGE_SIZE = 100
DEFAULT_MAX_PAGES = 100
DEFAULT_MAX_RESPONSE_BYTES = 1_000_000


class _NoRedirectHandler(HTTPRedirectHandler):
    """Reject redirects so authenticated requests cannot leave the configured origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _default_opener(request: Request, timeout: int) -> Any:
    return build_opener(_NoRedirectHandler()).open(request, timeout=timeout)


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
        raise ValueError("base_url must contain only the ERPNext HTTPS origin")
    if parsed.query or parsed.fragment:
        raise ValueError("base_url must not contain query parameters or fragments")
    if any(character.isspace() for character in parsed.netloc):
        raise ValueError("base_url hostname must not contain whitespace")

    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("base_url contains an invalid port") from exc

    return f"https://{parsed.netloc}"


def _require_non_empty(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty")


def _require_bounded_int(name: str, value: int, *, maximum: int) -> None:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError(f"{name} must be between 1 and {maximum}")


def _validate_resource(resource: str) -> None:
    if not isinstance(resource, str) or not resource.strip():
        raise ValueError("resource must be non-empty")
    if resource != resource.strip():
        raise ValueError("resource must not contain surrounding whitespace")
    if any(character in resource for character in "/?#"):
        raise ValueError("resource must be a simple configured read-only resource name")
    if any(ord(character) < 32 for character in resource):
        raise ValueError("resource must not contain control characters")


class ERPNextDiscoveryAdapter:
    """Bounded, GET-only Frappe REST discovery adapter.

    Authentication is supplied by the caller. Production networking rejects
    redirects, uses HTTPS only, issues GET requests only, and applies explicit
    pagination and response-size bounds.
    """

    def __init__(
        self,
        *,
        base_url: str,
        tenant_id: str,
        api_key: str,
        api_secret: str,
        resources: tuple[str, ...] = ("DocType",),
        page_size: int = DEFAULT_PAGE_SIZE,
        max_pages: int = DEFAULT_MAX_PAGES,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        _require_non_empty("tenant_id", tenant_id)
        _require_non_empty("api_key", api_key)
        _require_non_empty("api_secret", api_secret)

        if not resources:
            raise ValueError("at least one discovery resource is required")
        for resource in resources:
            _validate_resource(resource)

        _require_bounded_int("page_size", page_size, maximum=500)
        _require_bounded_int("max_pages", max_pages, maximum=1000)
        _require_bounded_int(
            "max_response_bytes",
            max_response_bytes,
            maximum=10_000_000,
        )
        _require_bounded_int("timeout_seconds", timeout_seconds, maximum=60)

        self._base_url = _normalize_base_url(base_url)
        self._tenant_id = tenant_id
        self._api_key = api_key
        self._api_secret = api_secret
        self._resources = resources
        self._page_size = page_size
        self._max_pages = max_pages
        self._max_response_bytes = max_response_bytes
        self._timeout_seconds = timeout_seconds
        self._opener = opener if opener is not None else _default_opener

    def discover(self) -> tuple[Observation, ...]:
        observations: list[Observation] = []

        for resource in self._resources:
            observations.extend(self._discover_resource(resource))

        return tuple(observations)

    def _discover_resource(self, resource: str) -> tuple[Observation, ...]:
        observations: list[Observation] = []
        observed_at = datetime.now().astimezone()

        for page_index in range(self._max_pages):
            limit_start = page_index * self._page_size
            rows = self._fetch_page(resource, limit_start=limit_start)

            for row in rows:
                observations.append(
                    Observation(
                        evidence=Evidence(
                            kind=EvidenceKind.API,
                            source="erpnext-read-only",
                            tenant_id=self._tenant_id,
                            observed_at=observed_at,
                            payload={"resource": resource, "record": row},
                        ),
                        mode=ObservationMode.READ_ONLY,
                    )
                )

            if len(rows) < self._page_size:
                return tuple(observations)

        raise RuntimeError(
            f"read-only discovery pagination limit reached before completeness for {resource}"
        )

    def _fetch_page(
        self,
        resource: str,
        *,
        limit_start: int,
    ) -> list[dict[str, Any]]:
        encoded_resource = quote(resource, safe="")
        query = urlencode(
            {
                "limit_start": limit_start,
                "limit_page_length": self._page_size,
            }
        )
        url = f"{self._base_url}/api/resource/{encoded_resource}?{query}"

        request = Request(
            url,
            headers={
                "Authorization": f"token {self._api_key}:{self._api_secret}",
                "Accept": "application/json",
            },
            method="GET",
        )

        try:
            with self._opener(request, timeout=self._timeout_seconds) as response:
                final_url_getter = getattr(response, "geturl", None)
                if callable(final_url_getter):
                    final_url = final_url_getter()
                    if final_url and final_url != request.full_url:
                        raise RuntimeError("read-only discovery redirects are not allowed")

                body = response.read(self._max_response_bytes + 1)
        except RuntimeError:
            raise
        except (HTTPError, URLError, TimeoutError) as exc:
            raise RuntimeError(
                f"read-only discovery failed for {resource}"
            ) from exc

        if len(body) > self._max_response_bytes:
            raise RuntimeError(
                f"read-only discovery response exceeds configured bound for {resource}"
            )

        try:
            payload: Mapping[str, Any] = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"read-only discovery returned invalid JSON for {resource}"
            ) from exc

        if not isinstance(payload, Mapping):
            raise TypeError("discovery response must be a JSON object")

        rows = payload.get("data")
        if not isinstance(rows, list):
            raise TypeError("discovery response data must be a list")

        if len(rows) > self._page_size:
            raise RuntimeError(
                f"discovery response exceeded requested page size for {resource}"
            )

        if any(not isinstance(row, dict) for row in rows):
            raise TypeError("discovery response rows must be JSON objects")

        return rows
