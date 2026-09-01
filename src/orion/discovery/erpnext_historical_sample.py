"""Bounded read-only historical sampling for ERPNext/Frappe.

This adapter intentionally samples a small submitted-document window.
Unlike full discovery, it performs exactly one list request and never
paginates toward resource completeness.

It contains no ERP write capability, persistence, promotion, or execution
authority.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request

from ..contracts import (
    Evidence,
    EvidenceKind,
    Observation,
    ObservationMode,
)
from .erpnext_adapter import (
    DEFAULT_TIMEOUT_SECONDS,
    _default_opener,
    _normalize_base_url,
    _require_bounded_int,
    _require_non_empty,
    _validate_resource,
)

DEFAULT_SAMPLE_SIZE = 5
MAX_SAMPLE_SIZE = 25
DEFAULT_SAMPLE_RESPONSE_BYTES = 250_000
MAX_SAMPLE_FIELDS = 25

_REQUIRED_AUDIT_FIELDS = frozenset(
    {
        "name",
        "company",
        "docstatus",
    }
)

_FIELD_PATTERN = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*$"
)


class ERPNextHistoricalSampleError(RuntimeError):
    """Raised when a historical sample violates its bounded contract."""


def _validate_field_name(field: str) -> None:
    if not isinstance(field, str) or not field:
        raise ValueError(
            "sample field names must be non-empty strings"
        )

    if not _FIELD_PATTERN.fullmatch(field):
        raise ValueError(
            f"unsafe sample field name: {field!r}"
        )


def _validate_order_by(
    order_by: str,
    *,
    allowed_fields: frozenset[str],
) -> None:
    if not isinstance(order_by, str):
        raise TypeError(
            "order_by must be a string"
        )

    clauses = order_by.split(",")

    if any(
        not clause.strip()
        for clause in clauses
    ):
        raise ValueError(
            "order_by contains an empty clause"
        )

    seen_fields: set[str] = set()

    for clause in clauses:
        parts = clause.strip().split()

        if len(parts) != 2:
            raise ValueError(
                "each order_by clause must contain "
                "one field and one direction"
            )

        field, direction = parts

        _validate_field_name(field)

        if field not in allowed_fields:
            raise ValueError(
                "order_by fields must also be "
                "requested sample fields"
            )

        if field in seen_fields:
            raise ValueError(
                "order_by fields must be unique"
            )

        seen_fields.add(field)

        if direction.lower() not in {
            "asc",
            "desc",
        }:
            raise ValueError(
                "order_by direction must be asc or desc"
            )


class ERPNextHistoricalSampleAdapter:
    """Read exactly one bounded submitted-document sample.

    The server request is company-scoped and submitted-only. Returned rows
    are independently checked again so ignored or malformed server filters
    cannot silently cross the configured company/status boundary.
    """

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
        sample_size: int = DEFAULT_SAMPLE_SIZE,
        order_by: str = "posting_date desc, name desc",
        max_response_bytes: int = DEFAULT_SAMPLE_RESPONSE_BYTES,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        opener: Callable[..., Any] | None = None,
    ) -> None:
        _require_non_empty(
            "tenant_id",
            tenant_id,
        )
        _require_non_empty(
            "api_key",
            api_key,
        )
        _require_non_empty(
            "api_secret",
            api_secret,
        )
        _require_non_empty(
            "company",
            company,
        )

        if company != company.strip():
            raise ValueError(
                "company must not contain surrounding whitespace"
            )

        _validate_resource(resource)

        if not fields:
            raise ValueError(
                "at least one sample field is required"
            )

        if len(fields) > MAX_SAMPLE_FIELDS:
            raise ValueError(
                f"sample fields exceed maximum of "
                f"{MAX_SAMPLE_FIELDS}"
            )

        for field in fields:
            _validate_field_name(field)

        if len(set(fields)) != len(fields):
            raise ValueError(
                "sample fields must be unique"
            )

        missing_audit_fields = (
            _REQUIRED_AUDIT_FIELDS
            - set(fields)
        )

        if missing_audit_fields:
            raise ValueError(
                "sample fields must include audit fields: "
                + ", ".join(
                    sorted(missing_audit_fields)
                )
            )

        _require_bounded_int(
            "sample_size",
            sample_size,
            maximum=MAX_SAMPLE_SIZE,
        )

        _require_bounded_int(
            "max_response_bytes",
            max_response_bytes,
            maximum=1_000_000,
        )

        _require_bounded_int(
            "timeout_seconds",
            timeout_seconds,
            maximum=60,
        )

        _validate_order_by(
            order_by,
            allowed_fields=frozenset(fields),
        )

        self._base_url = _normalize_base_url(
            base_url
        )
        self._tenant_id = tenant_id
        self._api_key = api_key
        self._api_secret = api_secret
        self._resource = resource
        self._company = company
        self._fields = fields
        self._sample_size = sample_size
        self._order_by = order_by
        self._max_response_bytes = max_response_bytes
        self._timeout_seconds = timeout_seconds
        self._opener = (
            opener
            if opener is not None
            else _default_opener
        )

    def discover(
        self,
    ) -> tuple[Observation, ...]:
        rows = self._fetch_sample()

        return tuple(
            Observation(
                evidence=Evidence(
                    kind=EvidenceKind.API,
                    source=(
                        "erpnext-historical-sample-read-only"
                    ),
                    tenant_id=self._tenant_id,
                    payload={
                        "resource": self._resource,
                        "record": row,
                    },
                ),
                mode=ObservationMode.READ_ONLY,
            )
            for row in rows
        )

    def _fetch_sample(
        self,
    ) -> list[dict[str, Any]]:
        encoded_resource = quote(
            self._resource,
            safe="",
        )

        fields = json.dumps(
            list(self._fields),
            separators=(",", ":"),
        )

        filters = json.dumps(
            [
                [
                    "company",
                    "=",
                    self._company,
                ],
                [
                    "docstatus",
                    "=",
                    1,
                ],
            ],
            separators=(",", ":"),
        )

        query = urlencode(
            {
                "fields": fields,
                "filters": filters,
                "order_by": self._order_by,
                "limit_start": 0,
                "limit_page_length": (
                    self._sample_size
                ),
            }
        )

        url = (
            f"{self._base_url}/api/resource/"
            f"{encoded_resource}?{query}"
        )

        request = Request(
            url,
            headers={
                "Authorization": (
                    f"token {self._api_key}:"
                    f"{self._api_secret}"
                ),
                "Accept": "application/json",
            },
            method="GET",
        )

        try:
            with self._opener(
                request,
                timeout=self._timeout_seconds,
            ) as response:
                final_url_getter = getattr(
                    response,
                    "geturl",
                    None,
                )

                if callable(final_url_getter):
                    final_url = final_url_getter()

                    if (
                        final_url
                        and final_url
                        != request.full_url
                    ):
                        raise ERPNextHistoricalSampleError(
                            "historical sample redirects "
                            "are not allowed"
                        )

                body = response.read(
                    self._max_response_bytes + 1
                )

        except ERPNextHistoricalSampleError:
            raise

        except (
            HTTPError,
            URLError,
            TimeoutError,
        ) as exc:
            raise ERPNextHistoricalSampleError(
                "read-only historical sample failed "
                f"for {self._resource}"
            ) from exc

        if len(body) > self._max_response_bytes:
            raise ERPNextHistoricalSampleError(
                "historical sample response exceeds "
                f"configured bound for {self._resource}"
            )

        try:
            payload: Mapping[str, Any] = (
                json.loads(
                    body.decode("utf-8")
                )
            )
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            raise ERPNextHistoricalSampleError(
                "historical sample returned "
                "invalid JSON"
            ) from exc

        if not isinstance(payload, Mapping):
            raise ERPNextHistoricalSampleError(
                "historical sample response "
                "must be a JSON object"
            )

        rows = payload.get("data")

        if not isinstance(rows, list):
            raise ERPNextHistoricalSampleError(
                "historical sample data "
                "must be a list"
            )

        if len(rows) > self._sample_size:
            raise ERPNextHistoricalSampleError(
                "historical sample exceeded "
                "requested record bound"
            )

        allowed_fields = set(self._fields)

        normalized: list[dict[str, Any]] = []
        seen_names: set[str] = set()

        for row in rows:
            if not isinstance(row, dict):
                raise ERPNextHistoricalSampleError(
                    "historical sample rows "
                    "must be JSON objects"
                )

            unexpected_fields = (
                set(row)
                - allowed_fields
            )

            if unexpected_fields:
                raise ERPNextHistoricalSampleError(
                    "historical sample returned "
                    "unrequested fields"
                )

            if not _REQUIRED_AUDIT_FIELDS.issubset(
                row
            ):
                raise ERPNextHistoricalSampleError(
                    "historical sample row "
                    "is missing audit fields"
                )

            missing_fields = (
                allowed_fields
                - set(row)
            )

            if missing_fields:
                raise ERPNextHistoricalSampleError(
                    "historical sample row "
                    "is missing requested fields"
                )

            name = row["name"]

            if (
                not isinstance(name, str)
                or not name.strip()
            ):
                raise ERPNextHistoricalSampleError(
                    "historical sample row "
                    "has invalid document identity"
                )

            if name in seen_names:
                raise ERPNextHistoricalSampleError(
                    "historical sample contains "
                    "duplicate document identity"
                )

            seen_names.add(name)

            if row["company"] != self._company:
                raise ERPNextHistoricalSampleError(
                    "historical sample crossed "
                    "company boundary"
                )

            docstatus = row["docstatus"]

            if (
                type(docstatus) is not int
                or docstatus != 1
            ):
                raise ERPNextHistoricalSampleError(
                    "historical sample contains "
                    "non-submitted document"
                )

            normalized.append(
                dict(row)
            )

        return normalized
