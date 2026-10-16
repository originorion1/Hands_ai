"""Bounded read-only sampling for non-submittable company records."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request

from ..contracts import Evidence, EvidenceKind, Observation, ObservationMode
from .erpnext_adapter import (
    DEFAULT_TIMEOUT_SECONDS,
    _default_opener,
    _normalize_base_url,
    _require_bounded_int,
    _require_non_empty,
    _validate_resource,
)
from .erpnext_historical_sample import (
    DEFAULT_SAMPLE_RESPONSE_BYTES,
    MAX_SAMPLE_FIELDS,
    MAX_SAMPLE_SIZE,
    _validate_field_name,
)

_REQUIRED_AUDIT_FIELDS = frozenset({"name", "company"})


class ERPNextCompanyRecordSampleError(RuntimeError):
    """Raised when a company record sample violates its bounded contract."""


class ERPNextCompanyRecordSampleAdapter:
    """Read exactly one bounded company-scoped non-submittable sample."""

    def __init__(
        self,
        *,
        base_url: str,
        tenant_id: str,
        api_key: str,
        api_secret: str,
        resource: str,
        company: str,
        fields: tuple[str, ...],
        sample_size: int,
        max_response_bytes: int = DEFAULT_SAMPLE_RESPONSE_BYTES,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        _require_non_empty("tenant_id", tenant_id)
        _require_non_empty("api_key", api_key)
        _require_non_empty("api_secret", api_secret)
        _require_non_empty("company", company)
        if company != company.strip():
            raise ValueError("company must not contain surrounding whitespace")
        _validate_resource(resource)
        if not fields:
            raise ValueError("at least one sample field is required")
        if len(fields) > MAX_SAMPLE_FIELDS:
            raise ValueError(f"sample fields exceed maximum of {MAX_SAMPLE_FIELDS}")
        for field in fields:
            _validate_field_name(field)
        if len(set(fields)) != len(fields):
            raise ValueError("sample fields must be unique")
        missing_audit_fields = _REQUIRED_AUDIT_FIELDS - set(fields)
        if missing_audit_fields:
            raise ValueError(
                "sample fields must include audit fields: "
                + ", ".join(sorted(missing_audit_fields))
            )
        _require_bounded_int("sample_size", sample_size, maximum=MAX_SAMPLE_SIZE)
        _require_bounded_int(
            "max_response_bytes", max_response_bytes, maximum=1_000_000
        )
        _require_bounded_int("timeout_seconds", timeout_seconds, maximum=60)

        self._base_url = _normalize_base_url(base_url)
        self._tenant_id = tenant_id
        self._api_key = api_key
        self._api_secret = api_secret
        self._resource = resource
        self._company = company
        self._fields = fields
        self._sample_size = sample_size
        self._max_response_bytes = max_response_bytes
        self._timeout_seconds = timeout_seconds
        self._opener = opener if opener is not None else _default_opener

    def discover(self) -> tuple[Observation, ...]:
        return tuple(
            Observation(
                evidence=Evidence(
                    kind=EvidenceKind.API,
                    source="erpnext-company-record-sample-read-only",
                    tenant_id=self._tenant_id,
                    payload={"resource": self._resource, "record": row},
                ),
                mode=ObservationMode.READ_ONLY,
            )
            for row in self._fetch_sample()
        )

    def _fetch_sample(self) -> list[dict[str, Any]]:
        query = urlencode(
            {
                "fields": json.dumps(list(self._fields), separators=(",", ":")),
                "filters": json.dumps(
                    [["company", "=", self._company]], separators=(",", ":")
                ),
                "order_by": "name desc",
                "limit_start": 0,
                "limit_page_length": self._sample_size,
            }
        )
        url = (
            f"{self._base_url}/api/resource/{quote(self._resource, safe='')}?{query}"
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
            with self._opener(request, timeout=self._timeout_seconds) as response:
                final_url_getter = getattr(response, "geturl", None)
                if callable(final_url_getter):
                    final_url = final_url_getter()
                    if final_url and final_url != request.full_url:
                        raise ERPNextCompanyRecordSampleError(
                            "company record sample redirects are not allowed"
                        )
                body = response.read(self._max_response_bytes + 1)
        except ERPNextCompanyRecordSampleError:
            raise
        except (HTTPError, URLError, TimeoutError) as exc:
            raise ERPNextCompanyRecordSampleError(
                f"company record sample failed for {self._resource}"
            ) from exc

        if len(body) > self._max_response_bytes:
            raise ERPNextCompanyRecordSampleError(
                f"company record sample response exceeds configured bound for {self._resource}"
            )
        try:
            payload: Mapping[str, Any] = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ERPNextCompanyRecordSampleError(
                "company record sample returned invalid JSON"
            ) from exc
        if not isinstance(payload, Mapping):
            raise ERPNextCompanyRecordSampleError(
                "company record sample response must be a JSON object"
            )
        rows = payload.get("data")
        if not isinstance(rows, list):
            raise ERPNextCompanyRecordSampleError(
                "company record sample data must be a list"
            )
        if len(rows) > self._sample_size:
            raise ERPNextCompanyRecordSampleError(
                "company record sample exceeded requested record bound"
            )

        allowed_fields = set(self._fields)
        normalized: list[dict[str, Any]] = []
        seen_names: set[str] = set()
        for row in rows:
            if not isinstance(row, Mapping):
                raise ERPNextCompanyRecordSampleError(
                    "company record sample rows must be JSON objects"
                )
            if set(row) - allowed_fields:
                raise ERPNextCompanyRecordSampleError(
                    "company record sample returned unrequested fields"
                )
            if allowed_fields - set(row):
                raise ERPNextCompanyRecordSampleError(
                    "company record sample row is missing requested fields"
                )
            name = row["name"]
            if not isinstance(name, str) or not name.strip():
                raise ERPNextCompanyRecordSampleError(
                    "company record sample row has invalid document identity"
                )
            if name in seen_names:
                raise ERPNextCompanyRecordSampleError(
                    "company record sample contains duplicate document identity"
                )
            seen_names.add(name)
            if row["company"] != self._company:
                raise ERPNextCompanyRecordSampleError(
                    "company record sample crossed company boundary"
                )
            normalized.append(dict(row))
        return normalized
