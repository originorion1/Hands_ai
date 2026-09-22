"""Tenant-bound, read-only projection of one aggregate live-session report."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..discovery.erpnext_live_session import (
    LiveSessionError,
    LiveSessionRunReport,
    live_session_run_report_from_json,
)
from ..stores.private_files import PrivateFileError, read_private_file

REPORT_INTERFACE_VERSION = "orion.readonly-report.v1"
MAX_BINDING_BYTES = 4 * 1024
MAX_REPORT_BYTES = 64 * 1024

_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_TENANT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_BINDING_KEYS = frozenset(
    {"binding_version", "report_path", "report_sha256", "tenant_id"}
)


class ReportInterfaceError(ValueError):
    """The local binding or its report cannot be safely presented."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise ReportInterfaceError("report binding keys must be unique")
        result[name] = value
    return result


def _absolute_unlinked_file(value: object, *, label: str) -> Path:
    if type(value) is not str or not value:
        raise ReportInterfaceError(f"{label} must be an absolute path")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ReportInterfaceError(f"{label} must be an absolute path")
    current = Path(path.anchor)
    try:
        for part in path.parts[1:]:
            current /= part
            if stat.S_ISLNK(os.lstat(current).st_mode):
                raise ReportInterfaceError(f"{label} cannot traverse symbolic links")
    except OSError as exc:
        raise ReportInterfaceError(f"{label} is unavailable") from exc
    return path


@dataclass(frozen=True, slots=True)
class BoundReportSource:
    """An immutable tenant and digest binding; tenant identity is never rendered."""

    tenant_id: str
    report_path: Path
    report_sha256: str

    @classmethod
    def from_private_binding(cls, path: Path) -> BoundReportSource:
        binding_path = _absolute_unlinked_file(str(path), label="report binding")
        try:
            body = read_private_file(
                binding_path,
                label="report binding",
                maximum_bytes=MAX_BINDING_BYTES,
            )
            payload = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object)
        except ReportInterfaceError:
            raise
        except (PrivateFileError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReportInterfaceError("report binding is invalid") from exc
        if type(payload) is not dict or set(payload) != _BINDING_KEYS:
            raise ReportInterfaceError("report binding fields do not match its schema")
        if payload["binding_version"] != REPORT_INTERFACE_VERSION:
            raise ReportInterfaceError("report binding version is unsupported")
        tenant_id = payload["tenant_id"]
        digest = payload["report_sha256"]
        if type(tenant_id) is not str or _TENANT.fullmatch(tenant_id) is None:
            raise ReportInterfaceError("report binding tenant is invalid")
        if type(digest) is not str or _DIGEST.fullmatch(digest) is None:
            raise ReportInterfaceError("report binding digest is invalid")
        report_path = _absolute_unlinked_file(payload["report_path"], label="report path")
        return cls(tenant_id=tenant_id, report_path=report_path, report_sha256=digest)

    def read_view(self) -> dict[str, object]:
        try:
            body = read_private_file(
                self.report_path,
                label="bound report",
                maximum_bytes=MAX_REPORT_BYTES,
            )
        except PrivateFileError as exc:
            raise ReportInterfaceError("bound report is unavailable") from exc
        actual = hashlib.sha256(body).hexdigest()
        if not hmac.compare_digest(actual, self.report_sha256):
            raise ReportInterfaceError("bound report digest does not match")
        try:
            report = live_session_run_report_from_json(body)
        except LiveSessionError as exc:
            raise ReportInterfaceError("bound report is invalid") from exc
        return project_report(report)


def project_report(report: LiveSessionRunReport) -> dict[str, object]:
    """Project only aggregate facts needed by the read-only interface."""

    return {
        "authority": {
            "erp_writes": report.erp_writes,
            "execution_allowed": report.execution_allowed,
            "promotion_allowed": report.promotion_allowed,
            "recommendation_allowed": report.recommendation_allowed,
        },
        "cycles": {
            "attempted": report.cycles_attempted,
            "completed": report.cycles_completed,
        },
        "failures": [
            {"category": category, "count": count}
            for category, count in report.failure_category_counts
        ],
        "interface_version": REPORT_INTERFACE_VERSION,
        "metadata": {
            "budget": report.metadata_get_budget,
            "used": report.metadata_gets,
        },
        "observations_persisted": report.observations_persisted,
        "report_kind": "completed_aggregate",
        "session_ended_at": report.session_ended_at.isoformat(),
        "session_started_at": report.session_started_at.isoformat(),
        "stop_reason": report.stop_reason,
        "studies": {
            "budget": report.study_get_budget,
            "used": report.study_gets,
        },
    }
