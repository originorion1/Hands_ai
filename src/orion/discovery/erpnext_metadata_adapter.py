"""Permission-aware ERPNext/Frappe metadata discovery.

This adapter uses the read-only whitelisted Frappe metadata method for
explicitly configured DocTypes. It does not enumerate the administrative
DocType resource and exposes no ERP mutation capability.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request

from ..contracts import Evidence, EvidenceKind, Observation, ObservationMode
from .erpnext_adapter import (
    DEFAULT_MAX_RESPONSE_BYTES,
    DEFAULT_TIMEOUT_SECONDS,
    _default_opener,
    _normalize_base_url,
    _require_bounded_int,
    _require_non_empty,
    _validate_resource,
)

DEFAULT_MAX_DOCTYPES = 100


class ERPNextMetadataAdapter:
    """Bounded, GET-only, permission-aware Frappe metadata adapter."""

    def __init__(
        self,
        *,
        base_url: str,
        tenant_id: str,
        api_key: str,
        api_secret: str,
        doctypes: tuple[str, ...],
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        _require_non_empty("tenant_id", tenant_id)
        _require_non_empty("api_key", api_key)
        _require_non_empty("api_secret", api_secret)

        if not doctypes:
            raise ValueError("at least one metadata DocType is required")
        if len(doctypes) > DEFAULT_MAX_DOCTYPES:
            raise ValueError(
                f"metadata DocType count must not exceed {DEFAULT_MAX_DOCTYPES}"
            )
        if len(set(doctypes)) != len(doctypes):
            raise ValueError("metadata DocTypes must be unique")

        for doctype in doctypes:
            _validate_resource(doctype)

        _require_bounded_int(
            "max_response_bytes",
            max_response_bytes,
            maximum=10_000_000,
        )
        _require_bounded_int(
            "timeout_seconds",
            timeout_seconds,
            maximum=60,
        )

        self._base_url = _normalize_base_url(base_url)
        self._tenant_id = tenant_id
        self._api_key = api_key
        self._api_secret = api_secret
        self._doctypes = doctypes
        self._max_response_bytes = max_response_bytes
        self._timeout_seconds = timeout_seconds
        self._opener = opener if opener is not None else _default_opener

    def discover(self) -> tuple[Observation, ...]:
        """Read metadata for explicitly configured DocTypes only."""

        observations: list[Observation] = []
        observed_at = datetime.now().astimezone()

        for doctype in self._doctypes:
            metadata = self._fetch_metadata(doctype)

            observations.append(
                Observation(
                    evidence=Evidence(
                        kind=EvidenceKind.METADATA,
                        source="erpnext-metadata-read-only",
                        tenant_id=self._tenant_id,
                        observed_at=observed_at,
                        payload={
                            "doctype": doctype,
                            "metadata": metadata,
                        },
                    ),
                    mode=ObservationMode.READ_ONLY,
                )
            )

        return tuple(observations)

    def _fetch_metadata(self, doctype: str) -> Mapping[str, Any]:
        query = urlencode({"doctype": doctype})
        url = (
            f"{self._base_url}"
            "/api/method/frappe.desk.form.load.getdoctype"
            f"?{query}"
        )

        request = Request(
            url,
            headers={
                "Authorization": f"token {self._api_key}:{self._api_secret}",
                "Accept": "application/json",
            },
            method="GET",
        )

        try:
            with self._opener(
                request,
                timeout=self._timeout_seconds,
            ) as response:
                final_url_getter = getattr(response, "geturl", None)

                if callable(final_url_getter):
                    final_url = final_url_getter()
                    if final_url and final_url != request.full_url:
                        raise RuntimeError(
                            "read-only metadata redirects are not allowed"
                        )

                body = response.read(self._max_response_bytes + 1)

        except RuntimeError:
            raise
        except (HTTPError, URLError, TimeoutError) as exc:
            raise RuntimeError(
                f"read-only metadata discovery failed for {doctype}"
            ) from exc

        if len(body) > self._max_response_bytes:
            raise RuntimeError(
                f"read-only metadata response exceeds configured bound for {doctype}"
            )

        try:
            payload: Any = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"read-only metadata returned invalid JSON for {doctype}"
            ) from exc

        if not isinstance(payload, Mapping):
            raise TypeError("metadata response must be a JSON object")

        return payload
