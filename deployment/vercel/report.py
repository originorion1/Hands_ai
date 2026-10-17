"""Constrained same-origin proxy to an independently authenticated report host.

No private report, ERP credential, tenant binding, or worker runs in Vercel.
The host verifies caller tokens and private report custody on every request.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import ssl
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlsplit

from .request_target import is_api_target

_MAX_REPORT_BYTES = 64 * 1024
_MAX_TOKEN_BYTES = 4096
_SAFE_CATEGORY = re.compile(r"[a-z_]{1,64}\Z")
_SAFE_TIMESTAMP = re.compile(r"[0-9T:+.\-Z]{1,64}\Z")
_SERVICE_TOKEN = re.compile(r"[A-Za-z0-9_-]{43}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_LINEAGE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_UUID = re.compile(r"[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}\Z")
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate report field")
        result[key] = value
    return result


def _counter(value: object) -> bool:
    return type(value) is int and value >= 0


def _validated_view(body: bytes) -> bytes:
    """Validate the public transport envelope before returning upstream bytes."""
    value = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object)
    if type(value) is dict and value.get("interface_version") == "orion.readonly-report.v3":
        return _validated_procurement_view(value, body)
    if type(value) is dict and value.get("interface_version") == "orion.readonly-report.v2":
        return _validated_lineage_view(value, body)
    if type(value) is not dict or set(value) != {
        "authority", "cycles", "failures", "interface_version", "metadata",
        "observations_persisted", "report_kind", "session_ended_at",
        "session_started_at", "stop_reason", "studies",
    }:
        raise ValueError("invalid report envelope")
    if (
        value["interface_version"] != "orion.readonly-report.v1"
        or value["report_kind"] != "completed_aggregate"
        or type(value["authority"]) is not dict
        or set(value["authority"]) != {
            "erp_writes", "execution_allowed", "promotion_allowed", "recommendation_allowed"
        }
        or type(value["authority"]["erp_writes"]) is not int
        or value["authority"]["erp_writes"] != 0
        or any(value["authority"][name] is not False for name in (
            "execution_allowed", "promotion_allowed", "recommendation_allowed"
        ))
        or not _counter(value["observations_persisted"])
        or type(value["stop_reason"]) is not str
        or _SAFE_CATEGORY.fullmatch(value["stop_reason"]) is None
    ):
        raise ValueError("invalid report authority")
    for name, keys in (
        ("cycles", {"attempted", "completed"}),
        ("metadata", {"budget", "used"}),
        ("studies", {"budget", "used"}),
    ):
        item = value[name]
        if type(item) is not dict or set(item) != keys or not all(_counter(v) for v in item.values()):
            raise ValueError("invalid report counters")
    if (
        value["cycles"]["completed"] > value["cycles"]["attempted"]
        or value["metadata"]["used"] > value["metadata"]["budget"]
        or value["studies"]["used"] > value["studies"]["budget"]
    ):
        raise ValueError("inconsistent report counters")
    for name in ("session_started_at", "session_ended_at"):
        if type(value[name]) is not str or _SAFE_TIMESTAMP.fullmatch(value[name]) is None:
            raise ValueError("invalid report timestamp")
    failures = value["failures"]
    if type(failures) is not list or len(failures) > 64:
        raise ValueError("invalid report failures")
    for item in failures:
        if (
            type(item) is not dict
            or set(item) != {"category", "count"}
            or type(item["category"]) is not str
            or _SAFE_CATEGORY.fullmatch(item["category"]) is None
            or not _counter(item["count"])
        ):
            raise ValueError("invalid report failure category")
    return body


def _validated_procurement_view(value: dict[str, object], body: bytes) -> bytes:
    """Allow only a small reviewer view over a valid v2 lineage envelope."""
    if set(value) != {
        "authority", "cycles", "failures", "interface_version", "lineage", "metadata",
        "observations_persisted", "report_kind", "session_ended_at", "session_started_at",
        "stop_reason", "studies", "procurement",
    } or value["report_kind"] != "procurement_price_leakage":
        raise ValueError("invalid procurement envelope")
    original = {key: part for key, part in value.items() if key != "procurement"}
    original["interface_version"] = "orion.readonly-report.v2"
    original["report_kind"] = "synthetic_lineage"
    _validated_lineage_view(original, body)
    finding = value["procurement"]
    required = {
        "target_ref", "company", "item", "normalized_unit", "currency", "quantity_each",
        "target_unit_usd", "comparison_refs", "excluded_evidence", "missing_evidence",
        "method", "alternative_explanations", "estimated_exposure_usd", "reviewer_decision",
        "independently_verified_savings_usd", "status", "confidence", "next_question",
        "finding_sha256", "base_report_sha256", "source_system", "authorization_status",
        "evidence_acquired_at", "evidence_ids", "authorization_id", "tenant_id",
        "run_id", "source_data_sha256", "invalidation_history",
        "review_event_sha256",
    }
    optional = {"historical_median_unit_usd", "reference_unit_usd", "supplier_comparison"}
    if (type(finding) is not dict or not required <= set(finding) or
            set(finding) - required - optional):
        raise ValueError("invalid procurement fields")
    if (finding["status"] not in {"candidate_review", "abstained", "no_anomaly"}
            or finding["source_system"] != "synthetic-records"
            or finding["authorization_status"] != "read_only_admitted"
            or finding["reviewer_decision"] not in {
                "pending", "accepted_for_followup", "rejected", "needs_evidence"
            }
            or (finding["review_event_sha256"] is not None and (
                type(finding["review_event_sha256"]) is not str or
                _DIGEST.fullmatch(finding["review_event_sha256"]) is None
            ))
            or finding["independently_verified_savings_usd"] is not None
            or finding["invalidation_history"] != []
            or finding["normalized_unit"] != "each" or finding["currency"] != "USD"
            or finding["run_id"] != value["lineage"]["run_id"]
            or finding["source_data_sha256"] != value["lineage"]["source_sha256"]
            or finding["authorization_id"] != value["lineage"]["authorization_id"]
            or finding["evidence_ids"] != value["lineage"]["evidence_ids"]
            or type(finding["next_question"]) not in (str, type(None))
            or type(finding["tenant_id"]) is not str or
            _LINEAGE_ID.fullmatch(finding["tenant_id"]) is None
            or type(finding["evidence_acquired_at"]) is not str or
            _SAFE_TIMESTAMP.fullmatch(finding["evidence_acquired_at"]) is None
            or any(type(finding[key]) is not str or len(finding[key]) > 256 for key in (
                "target_ref", "company", "item", "quantity_each", "target_unit_usd",
                "estimated_exposure_usd", "method", "confidence"
            ))
            or any(type(finding[key]) is not str or _DIGEST.fullmatch(finding[key]) is None
                   for key in ("finding_sha256", "base_report_sha256", "source_data_sha256"))
            or any(type(finding[key]) is not list or len(finding[key]) > 100 or
                   any(type(item) is not str or len(item) > 256 for item in finding[key])
                   for key in ("comparison_refs", "excluded_evidence", "missing_evidence",
                               "alternative_explanations"))):
        raise ValueError("unsafe procurement view")
    if (finding["next_question"] is not None and len(finding["next_question"]) > 256 or
            "supplier_comparison" in finding and (
                type(finding["supplier_comparison"]) is not list or
                len(finding["supplier_comparison"]) > 100 or
                any(type(item) is not str or len(item) > 128
                    for item in finding["supplier_comparison"])
            ) or any(type(finding[key]) is not str or
                     re.fullmatch(r"[0-9]{1,16}\.[0-9]{2}", finding[key]) is None
                     for key in ("target_unit_usd", "estimated_exposure_usd")
                     if key in finding)):
        raise ValueError("invalid procurement arithmetic")
    return body


def _validated_lineage_view(value: dict[str, object], body: bytes) -> bytes:
    """Accept only the bounded synthetic provenance extension of the safe view."""
    if set(value) != {
        "authority", "cycles", "failures", "interface_version", "lineage", "metadata",
        "observations_persisted", "report_kind", "session_ended_at", "session_started_at",
        "stop_reason", "studies",
    } or value["report_kind"] != "synthetic_lineage" or value["stop_reason"] != "cycle_limit":
        raise ValueError("invalid lineage envelope")
    authority = value["authority"]
    cycles = value["cycles"]
    if (type(authority) is not dict or set(authority) != {
            "erp_writes", "execution_allowed", "promotion_allowed", "recommendation_allowed"
    } or type(authority["erp_writes"]) is not int or authority["erp_writes"] != 0
            or any(authority[name] is not False for name in (
                "execution_allowed", "promotion_allowed", "recommendation_allowed"
            )) or type(cycles) is not dict or set(cycles) != {"attempted", "completed"}
            or any(type(cycles[name]) is not int or cycles[name] != 1 for name in cycles)
            or type(value["failures"]) is not list or value["failures"]):
        raise ValueError("unsafe lineage authority")
    for name in ("metadata", "studies"):
        item = value[name]
        if (type(item) is not dict or set(item) != {"budget", "used"}
                or not all(_counter(part) for part in item.values())
                or item["used"] > item["budget"]):
            raise ValueError("invalid lineage counters")
    if not _counter(value["observations_persisted"]):
        raise ValueError("invalid lineage count")
    for name in ("session_started_at", "session_ended_at"):
        stamp = value[name]
        if type(stamp) is not str or _SAFE_TIMESTAMP.fullmatch(stamp) is None:
            raise ValueError("invalid lineage timestamp")
    lineage = value["lineage"]
    if type(lineage) is not dict or set(lineage) != {
        "run_id", "dataset_id", "source_sha256", "authorization_id", "evidence_ids",
        "investigation_sha256", "witness_sha256",
    }:
        raise ValueError("invalid lineage fields")
    if (type(lineage["run_id"]) is not str or _UUID.fullmatch(lineage["run_id"]) is None
            or any(type(lineage[name]) is not str or _LINEAGE_ID.fullmatch(lineage[name]) is None
                   for name in ("dataset_id", "authorization_id"))
            or any(type(lineage[name]) is not str or _DIGEST.fullmatch(lineage[name]) is None
                   for name in ("source_sha256", "investigation_sha256", "witness_sha256"))
            or type(lineage["evidence_ids"]) is not list
            or not 1 <= len(lineage["evidence_ids"]) <= 100
            or len(set(lineage["evidence_ids"])) != len(lineage["evidence_ids"])
            or any(type(item) is not str or _UUID.fullmatch(item) is None
                   for item in lineage["evidence_ids"])
            or len(lineage["evidence_ids"]) != value["observations_persisted"]):
        raise ValueError("invalid lineage identity")
    return body


def _upstream_url(value: str | None) -> tuple[str, int] | None:
    if not value:
        return None
    try:
        target = urlsplit(value)
        port = target.port
    except ValueError:
        return None
    if (
        target.scheme != "https"
        or not target.hostname
        or target.username is not None
        or target.password is not None
        or target.query
        or target.fragment
        or target.path != "/api/v1/report"
        or port == 0
    ):
        return None
    return target.hostname, port or 443


def _service_headers(host: str, token: str | None) -> dict[str, str] | None:
    """Require a distinct server-side credential for every non-loopback origin."""
    if token is None and host in _LOCAL_HOSTS:
        return {}
    if type(token) is not str or _SERVICE_TOKEN.fullmatch(token) is None:
        return None
    return {"X-ORION-Service-Token": token}


def fetch_report(
    authorization: str,
    *,
    backend_url: str | None,
    service_token: str | None = None,
    connection_factory: type[http.client.HTTPSConnection] = http.client.HTTPSConnection,
) -> tuple[HTTPStatus, bytes]:
    upstream = _upstream_url(backend_url)
    if upstream is None:
        return HTTPStatus.SERVICE_UNAVAILABLE, b'{"error":"report_service_not_connected"}\n'
    host, port = upstream
    service_headers = _service_headers(host, service_token)
    if service_headers is None:
        return HTTPStatus.SERVICE_UNAVAILABLE, b'{"error":"report_service_not_connected"}\n'
    try:
        connection = connection_factory(host, port=port, timeout=5, context=ssl.create_default_context())
        try:
            connection.request(
                "GET", "/api/v1/report",
                headers={
                    "Authorization": authorization, "Accept": "application/json",
                    **service_headers,
                },
            )
            response = connection.getresponse()
            status = response.status
            body = response.read(_MAX_REPORT_BYTES + 1)
        finally:
            connection.close()
    except (OSError, ssl.SSLError, http.client.HTTPException, TimeoutError):
        return HTTPStatus.SERVICE_UNAVAILABLE, b'{"error":"report_service_unavailable"}\n'
    if status in {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN}:
        return HTTPStatus.UNAUTHORIZED, b'{"error":"access_denied"}\n'
    if status != HTTPStatus.OK or len(body) > _MAX_REPORT_BYTES:
        return HTTPStatus.SERVICE_UNAVAILABLE, b'{"error":"report_service_unavailable"}\n'
    try:
        return HTTPStatus.OK, _validated_view(body)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        return HTTPStatus.SERVICE_UNAVAILABLE, b'{"error":"report_service_invalid"}\n'


def probe_backend(
    backend_url: str | None,
    *,
    service_token: str | None = None,
    connection_factory: type[http.client.HTTPSConnection] = http.client.HTTPSConnection,
) -> bool:
    """Check the separate report host without credentials or report contents."""
    upstream = _upstream_url(backend_url)
    if upstream is None:
        return False
    host, port = upstream
    service_headers = _service_headers(host, service_token)
    if service_headers is None:
        return False
    try:
        connection = connection_factory(host, port=port, timeout=3, context=ssl.create_default_context())
        try:
            connection.request(
                "GET", "/ready", headers={"Accept": "application/json", **service_headers}
            )
            response = connection.getresponse()
            body = response.read(257)
            return (
                response.status == HTTPStatus.OK
                and len(body) <= 256
                and json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object)
                == {"ready": True, "execution_allowed": False}
            )
        finally:
            connection.close()
    except (OSError, ssl.SSLError, http.client.HTTPException, TimeoutError,
            UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return False


class ReportHandler(BaseHTTPRequestHandler):
    """Read-only route; caller token is forwarded only to the fixed HTTPS host."""

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def _reply(self, code: HTTPStatus, body: bytes, *, head: bool = False) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _get(self, *, head: bool = False) -> None:
        if not is_api_target(self.path, "/api/v1/report"):
            self._reply(HTTPStatus.NOT_FOUND, b'{"error":"not_found"}\n', head=head)
            return
        values = self.headers.get_all("Authorization") or []
        authorization = values[0] if len(values) == 1 else None
        if (
            type(authorization) is not str
            or not authorization.startswith("Bearer ")
            or not 7 < len(authorization) <= _MAX_TOKEN_BYTES + 7
        ):
            self._reply(HTTPStatus.UNAUTHORIZED, b'{"error":"access_denied"}\n', head=head)
            return
        status, body = fetch_report(
            authorization, backend_url=os.environ.get("ORION_REPORT_BACKEND_URL"),
            service_token=os.environ.get("ORION_REPORT_SERVICE_TOKEN"),
        )
        self._reply(status, body, head=head)

    def do_GET(self) -> None:
        self._get()

    def do_HEAD(self) -> None:
        self._get(head=True)

    def do_POST(self) -> None:
        self._reply(HTTPStatus.METHOD_NOT_ALLOWED, b'{"error":"method_not_allowed"}\n')

    do_PUT = do_POST
    do_PATCH = do_POST
    do_DELETE = do_POST
    do_OPTIONS = do_POST
