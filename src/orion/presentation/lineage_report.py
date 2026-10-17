"""Fail-closed projection of one private synthetic source-to-report lineage.

The public view contains identifiers and aggregate counters, never records.
The private witness retains the original source rows and admitted evidence.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from pathlib import Path
from typing import Any
from uuid import UUID

from ..stores.private_files import PrivateFileError, read_private_file

LINEAGE_INTERFACE_VERSION = "orion.readonly-report.v2"
LINEAGE_REPORT_SCHEMA = "orion.synthetic-lineage-report.v1"
LINEAGE_WITNESS_SCHEMA = "orion.synthetic-lineage-witness.v1"
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_VIEW_KEYS = frozenset({
    "authority", "cycles", "failures", "interface_version", "lineage", "metadata",
    "observations_persisted", "report_kind", "session_ended_at", "session_started_at",
    "stop_reason", "studies",
})
_LINEAGE_KEYS = frozenset({
    "run_id", "dataset_id", "source_sha256", "authorization_id", "evidence_ids",
    "investigation_sha256", "witness_sha256",
})


class LineageReportError(ValueError):
    """Private lineage or safe public projection is invalid."""


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise LineageReportError("duplicate lineage field")
        result[key] = value
    return result


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _id(value: object) -> bool:
    return type(value) is str and _IDENTITY.fullmatch(value) is not None


def _digest(value: object) -> bool:
    return type(value) is str and _DIGEST.fullmatch(value) is not None


def _uuid(value: object) -> bool:
    if type(value) is not str:
        return False
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def _counter(value: object) -> bool:
    return type(value) is int and 0 <= value <= 1_000_000


def _validate_view(view: object) -> dict[str, object]:
    if type(view) is not dict or set(view) != _VIEW_KEYS:
        raise LineageReportError("invalid lineage view fields")
    if (view["interface_version"] != LINEAGE_INTERFACE_VERSION
            or view["report_kind"] != "synthetic_lineage"
            or view["stop_reason"] != "cycle_limit"
            or type(view["failures"]) is not list or view["failures"]
            or not all(type(view[key]) is str and len(view[key]) <= 64 for key in
                       ("session_started_at", "session_ended_at"))):
        raise LineageReportError("invalid lineage view contract")
    authority = view["authority"]
    if (type(authority) is not dict or set(authority) != {
            "erp_writes", "execution_allowed", "promotion_allowed", "recommendation_allowed"
    } or type(authority["erp_writes"]) is not int or authority["erp_writes"] != 0
            or any(authority[name] is not False for name in (
                "execution_allowed", "promotion_allowed", "recommendation_allowed"
            ))):
        raise LineageReportError("unsafe lineage authority")
    for key, fields in (("cycles", {"attempted", "completed"}),
                        ("metadata", {"budget", "used"}),
                        ("studies", {"budget", "used"})):
        item = view[key]
        if type(item) is not dict or set(item) != fields or not all(
                _counter(value) for value in item.values()):
            raise LineageReportError("invalid lineage counters")
    if (view["cycles"] != {"attempted": 1, "completed": 1}
            or view["metadata"]["used"] > view["metadata"]["budget"]
            or view["studies"]["used"] > view["studies"]["budget"]
            or not _counter(view["observations_persisted"])):
        raise LineageReportError("inconsistent lineage counters")
    lineage = view["lineage"]
    if (type(lineage) is not dict or set(lineage) != _LINEAGE_KEYS
            or not _uuid(lineage["run_id"])
            or not _id(lineage["dataset_id"])
            or not _id(lineage["authorization_id"])
            or not all(_digest(lineage[key]) for key in
                       ("source_sha256", "investigation_sha256", "witness_sha256"))
            or type(lineage["evidence_ids"]) is not list
            or not lineage["evidence_ids"]
            or len(lineage["evidence_ids"]) > 100
            or len(set(lineage["evidence_ids"])) != len(lineage["evidence_ids"])
            or not all(_uuid(item) for item in lineage["evidence_ids"])):
        raise LineageReportError("invalid lineage identity")
    if view["observations_persisted"] != len(lineage["evidence_ids"]):
        raise LineageReportError("lineage count mismatch")
    return view


def project_lineage_report(body: bytes, *, tenant_id: str, report_path: Path) -> dict[str, object]:
    """Verify report, witness, original records and investigation before projection."""
    try:
        report = json.loads(body.decode("utf-8"), object_pairs_hook=_object)
        if type(report) is not dict or set(report) != {
                "schema", "tenant_id", "witness_path", "witness_sha256", "view"
        } or report["schema"] != LINEAGE_REPORT_SCHEMA or report["tenant_id"] != tenant_id:
            raise LineageReportError("report tenant or schema mismatch")
        view = _validate_view(report["view"])
        path = Path(report["witness_path"])
        if (not path.is_absolute() or ".." in path.parts or path.parent != report_path.parent
                or path.name != "witness.json" or path.is_symlink()):
            raise LineageReportError("witness path denied")
        witness_body = read_private_file(path, label="lineage witness", maximum_bytes=64 * 1024)
        if (not _digest(report["witness_sha256"])
                or not hmac.compare_digest(hashlib.sha256(witness_body).hexdigest(),
                                           report["witness_sha256"])):
            raise LineageReportError("witness digest mismatch")
        witness = json.loads(witness_body.decode("utf-8"), object_pairs_hook=_object)
    except (UnicodeError, json.JSONDecodeError, TypeError, OSError, PrivateFileError) as exc:
        raise LineageReportError("lineage custody unavailable") from exc
    if type(witness) is not dict or set(witness) != {
            "schema", "tenant_id", "run_id", "dataset_id", "source_sha256",
            "source_records", "authorization_id", "evidence", "investigation",
            "source_path", "source_file_sha256", "evidence_path", "evidence_file_sha256",
    } or witness["schema"] != LINEAGE_WITNESS_SCHEMA or witness["tenant_id"] != tenant_id:
        raise LineageReportError("invalid lineage witness")
    lineage = view["lineage"]
    if (lineage["run_id"], lineage["dataset_id"], lineage["source_sha256"],
            lineage["authorization_id"], lineage["witness_sha256"]) != (
            witness["run_id"], witness["dataset_id"], witness["source_sha256"],
            witness["authorization_id"], report["witness_sha256"]):
        raise LineageReportError("lineage witness identity mismatch")
    source = witness["source_records"]
    evidence = witness["evidence"]
    investigation = witness["investigation"]
    try:
        for label, expected in (("source", source), ("evidence", evidence)):
            path = Path(witness[f"{label}_path"])
            if (not path.is_absolute() or ".." in path.parts
                    or path.parent != report_path.parent or path.name != f"{label}.json"
                    or path.is_symlink() or not _digest(witness[f"{label}_file_sha256"])):
                raise LineageReportError("original evidence path denied")
            original = read_private_file(path, label=label, maximum_bytes=64 * 1024)
            original_view = json.loads(original.decode("utf-8"), object_pairs_hook=_object)
            expected_view = {
                "tenant_id": tenant_id, "run_id": lineage["run_id"],
                "dataset_id": lineage["dataset_id"],
                "source_sha256": lineage["source_sha256"], label: expected,
            }
            if (not hmac.compare_digest(hashlib.sha256(original).hexdigest(),
                                        witness[f"{label}_file_sha256"])
                    or original_view != expected_view):
                raise LineageReportError("original evidence changed")
    except (PrivateFileError, UnicodeError, json.JSONDecodeError, OSError,
            TypeError) as exc:
        raise LineageReportError("original evidence unavailable") from exc
    if (type(source) is not list or type(evidence) is not list
            or len(source) != len(evidence) or len(evidence) != len(lineage["evidence_ids"])
            or hashlib.sha256(_canonical(source)).hexdigest() != lineage["source_sha256"]
            or type(investigation) is not dict or set(investigation) != {
                "run_id", "tenant_id", "dataset_id", "source_sha256",
                "authorization_id", "evidence_ids", "hypotheses"
            } or investigation["run_id"] != lineage["run_id"]
            or investigation["tenant_id"] != tenant_id
            or investigation["dataset_id"] != lineage["dataset_id"]
            or investigation["source_sha256"] != lineage["source_sha256"]
            or investigation["authorization_id"] != lineage["authorization_id"]
            or investigation["evidence_ids"] != lineage["evidence_ids"]
            or hashlib.sha256(_canonical(investigation)).hexdigest()
            != lineage["investigation_sha256"]):
        raise LineageReportError("lineage evidence or investigation mismatch")
    for record, item, evidence_id in zip(source, evidence, lineage["evidence_ids"]):
        if (type(record) is not dict or type(item) is not dict
                or set(item) != {"evidence_id", "record", "authorization_id", "source_sha256",
                                 "observed_at", "kind", "source", "mode", "provenance"}
                or item["evidence_id"] != evidence_id or item["record"] != record
                or item["authorization_id"] != lineage["authorization_id"]
                or item["source_sha256"] != lineage["source_sha256"]
                or item["kind"] != "source" or item["source"] != "synthetic-record-v1"
                or item["mode"] != "read_only"
                or type(item["observed_at"]) is not str
                or type(item["provenance"]) is not dict
                or set(item["provenance"]) != {
                    "authorization_id", "scope_sha256", "source_id", "source_record_id",
                    "upstream_evidence_id", "upstream_observation_id"
                } or item["provenance"]["authorization_id"] != lineage["authorization_id"]
                or not _digest(item["provenance"]["scope_sha256"])
                or not _uuid(item["provenance"]["upstream_evidence_id"])
                or not _uuid(item["provenance"]["upstream_observation_id"])):
            raise LineageReportError("lineage evidence mismatch")
    hypotheses = investigation["hypotheses"]
    if (type(hypotheses) is not list or not hypotheses
            or any(type(item) is not dict or set(item) != {"status", "supporting_evidence"}
                   or item["status"] != "validated"
                   or type(item["supporting_evidence"]) is not list
                   or not item["supporting_evidence"]
                   or not set(item["supporting_evidence"]) <= set(lineage["evidence_ids"])
                   for item in hypotheses)):
        raise LineageReportError("investigation provenance mismatch")
    return view
