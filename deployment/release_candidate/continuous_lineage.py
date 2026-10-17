"""Offline composition of one authorized synthetic read into a bound report.

This is an operator-side acceptance path, not a service or source of grants.
It uses the existing one-use pilot read authorization and report-host contracts.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID

from orion.discovery.pilot_read import (
    PilotAuthorization,
    PilotRequest,
    launch_pilot_read,
)
from orion.presentation.lineage_report import (
    LINEAGE_INTERFACE_VERSION,
    LINEAGE_REPORT_SCHEMA,
    LINEAGE_WITNESS_SCHEMA,
)
from orion.presentation.report_interface import REPORT_INTERFACE_VERSION
from orion.stores.memory import InMemoryEvidenceStore
from orion.understanding.hypotheses import generate_hypotheses
from orion.validation.claims import Assurance, validate_hypothesis
from orion_connector_sdk import CONTRACT_VERSION, ConnectorRegistration
from orion_connector_synthetic import SyntheticRecordConnector


def _json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       allow_nan=False) + "\n").encode("utf-8")


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_private(path: Path, body: bytes) -> None:
    if len(body) > 64 * 1024:
        raise ValueError("lineage artifact exceeds bound")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())


def _investigate(observations: tuple) -> list[dict[str, object]]:
    hypotheses = generate_hypotheses(observations)
    if len(hypotheses) != len(observations) or not hypotheses:
        raise ValueError("synthetic investigation lacks acquired provenance")
    result = []
    for hypothesis in hypotheses:
        decision = validate_hypothesis(
            hypothesis, assurance=Assurance.LOW, independent_evidence_count=1
        )
        if decision.status != "validated":
            raise ValueError("synthetic investigation did not validate")
        result.append({
            "status": decision.status,
            "supporting_evidence": [str(value) for value in hypothesis.supporting_evidence],
        })
    return result


@dataclass(frozen=True, slots=True)
class LineageResult:
    tenant_id: str
    run_id: UUID
    dataset_id: str
    source_sha256: str
    evidence_ids: tuple[str, ...]
    witness_sha256: str
    report_sha256: str
    binding_path: Path
    view: dict[str, object]


def run_synthetic_lineage(
    *,
    registration: ConnectorRegistration,
    connector: SyntheticRecordConnector,
    request: PilotRequest,
    authorization_id: str,
    lookup: Callable[[str], PilotAuthorization | None],
    run_id: UUID,
    private_dir: Path,
    now: datetime,
) -> LineageResult:
    """Persist source/evidence before investigation; publish report only on success."""
    if (not isinstance(registration, ConnectorRegistration)
            or registration.name != "synthetic-records"
            or registration.distribution != "originorion-connector-synthetic"
            or registration.version != "0.2.0rc2"
            or registration.contract_version != CONTRACT_VERSION
            or registration.entry_point
            != "orion_connector_synthetic:SyntheticRecordConnector"
            or registration.execution_allowed is not False):
        raise ValueError("reviewed synthetic connector required")
    if (not isinstance(connector, SyntheticRecordConnector)
            or type(run_id) is not UUID or not isinstance(request, PilotRequest)
            or request.tenant_id != connector.tenant_id
            or request.source_id != connector.source_id):
        raise ValueError("synthetic run scope mismatch")
    if (not isinstance(now, datetime) or now.tzinfo is None
            or now.utcoffset() is None or not private_dir.is_absolute()
            or private_dir.resolve(strict=True) != private_dir):
        raise ValueError("private run directory or clock invalid")
    info = private_dir.stat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise ValueError("private run directory unavailable")
    if any(private_dir.iterdir()):
        raise ValueError("private run directory must be empty")

    observations = launch_pilot_read(
        request, authorization_id=authorization_id, lookup=lookup,
        adapter=connector, clock=lambda: now,
    )
    if not observations or any(
            item.evidence.observed_at > now
            or now - item.evidence.observed_at > timedelta(minutes=5)
            for item in observations):
        raise ValueError("stale or missing synthetic evidence")
    evidence_ids = tuple(str(item.evidence.evidence_id) for item in observations)
    if len(set(evidence_ids)) != len(evidence_ids):
        raise ValueError("duplicate synthetic evidence")
    source_records = connector.source_records()
    source_body = _json({
        "tenant_id": request.tenant_id, "run_id": str(run_id),
        "dataset_id": connector.dataset_id, "source_sha256": connector.source_sha256,
        "source": source_records,
    })
    _write_private(private_dir / "source.json", source_body)
    store = InMemoryEvidenceStore()
    store.append_batch_atomic(tuple(item.evidence for item in observations))
    snapshots = store.query(tenant_id=request.tenant_id)
    if tuple(item.evidence_id for item in snapshots) != tuple(
            item.evidence.evidence_id for item in observations):
        raise ValueError("evidence custody mismatch")
    evidence = []
    for item in snapshots:
        provenance = item.payload["provenance"]
        if provenance["authorization_id"] != authorization_id:
            raise ValueError("admitted evidence authorization mismatch")
        evidence.append({
            "evidence_id": str(item.evidence_id),
            "record": dict(item.payload["record"]),
            "authorization_id": provenance["authorization_id"],
            "source_sha256": connector.source_sha256,
            "observed_at": item.observed_at.isoformat(),
            "kind": item.kind.value,
            "source": item.source,
            "mode": "read_only",
            "provenance": dict(provenance),
        })
    evidence_body = _json({
        "tenant_id": request.tenant_id, "run_id": str(run_id),
        "dataset_id": connector.dataset_id, "source_sha256": connector.source_sha256,
        "evidence": evidence,
    })
    _write_private(private_dir / "evidence.json", evidence_body)

    investigation = {
        "run_id": str(run_id), "tenant_id": request.tenant_id,
        "dataset_id": connector.dataset_id,
        "source_sha256": connector.source_sha256,
        "authorization_id": authorization_id,
        "evidence_ids": list(evidence_ids),
        "hypotheses": _investigate(observations),
    }
    investigation_sha256 = _digest(_json(investigation).rstrip(b"\n"))
    witness = {
        "schema": LINEAGE_WITNESS_SCHEMA,
        "tenant_id": request.tenant_id, "run_id": str(run_id),
        "dataset_id": connector.dataset_id, "source_sha256": connector.source_sha256,
        "source_records": source_records,
        "authorization_id": authorization_id,
        "evidence": evidence,
        "investigation": investigation,
        "source_path": str(private_dir / "source.json"),
        "source_file_sha256": _digest(source_body),
        "evidence_path": str(private_dir / "evidence.json"),
        "evidence_file_sha256": _digest(evidence_body),
    }
    witness_body = _json(witness)
    witness_sha256 = _digest(witness_body)
    _write_private(private_dir / "witness.json", witness_body)
    view = {
        "interface_version": LINEAGE_INTERFACE_VERSION,
        "report_kind": "synthetic_lineage",
        "authority": {"erp_writes": 0, "execution_allowed": False,
                      "promotion_allowed": False, "recommendation_allowed": False},
        "cycles": {"attempted": 1, "completed": 1},
        "metadata": {"budget": 0, "used": 0},
        "studies": {"budget": request.max_records, "used": len(observations)},
        "observations_persisted": len(snapshots),
        "failures": [], "stop_reason": "cycle_limit",
        "session_started_at": now.isoformat(), "session_ended_at": now.isoformat(),
        "lineage": {
            "run_id": str(run_id), "dataset_id": connector.dataset_id,
            "source_sha256": connector.source_sha256,
            "authorization_id": authorization_id,
            "evidence_ids": list(evidence_ids),
            "investigation_sha256": investigation_sha256,
            "witness_sha256": witness_sha256,
        },
    }
    report_body = _json({
        "schema": LINEAGE_REPORT_SCHEMA, "tenant_id": request.tenant_id,
        "witness_path": str(private_dir / "witness.json"),
        "witness_sha256": witness_sha256, "view": view,
    })
    report_sha256 = _digest(report_body)
    _write_private(private_dir / "report.json", report_body)
    _write_private(private_dir / "binding.json", _json({
        "binding_version": REPORT_INTERFACE_VERSION,
        "tenant_id": request.tenant_id,
        "report_path": str(private_dir / "report.json"),
        "report_sha256": report_sha256,
    }))
    return LineageResult(
        request.tenant_id, run_id, connector.dataset_id, connector.source_sha256,
        evidence_ids, witness_sha256, report_sha256, private_dir / "binding.json", view,
    )
