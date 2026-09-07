"""Offline, immutable investigation memory over validated retained snapshots.

This adapter never acquires ERP data. Reload recomputes the descriptive result
against the exact retained snapshot; it does not elevate a stored finding to a
business verdict. New snapshots have new artifacts; older memory is preserved.
"""
from __future__ import annotations

import fcntl
import json
import os
from dataclasses import asdict
from uuid import uuid4

from ..learning.retained_quality_investigation import investigate_retained_missing_values
from . import erpnext_learning_comparison as comparison
from .erpnext_adapter import DEFAULT_MAX_RESPONSE_BYTES
from .erpnext_live_session import _destination_ready, _study_authorization
from .erpnext_metadata_preflight import _write_private_json
from .erpnext_metadata_refresh import _read_private_bytes


def _artifact(inputs):
    live = inputs.live()
    if len(live.company_authorization.companies) != 1:
        raise comparison.LearningComparisonError("investigation requires one exact company")
    rows, checkpoints, batches, understanding = comparison._state(inputs)
    company = live.company_authorization.companies[0]
    result = investigate_retained_missing_values(
        understanding, batches, authorization=_study_authorization(live), company=company,
    )
    binding = {
        "source_commit": inputs.source_commit,
        "candidate_sha256": inputs.candidate_sha256,
        "authorization_reference": inputs.authorization_reference,
        "tenant_id": live.tenant_id,
        "company": company,
        "scopes": [[scope.entity, list(scope.fields)] for scope in live.reviewed_scopes],
        "evidence_sha256": comparison._json_digest(rows),
        "checkpoints_sha256": comparison._json_digest(checkpoints),
    }
    payload = {
        "schema": 1,
        "investigation_kind": "retained_missing_values_v1",
        "binding": binding,
        "finding": asdict(result.finding) if result.finding else None,
        "aggregate": result.aggregate(),
    }
    # Convert private UUID provenance to stable JSON, without copying raw values.
    payload = json.loads(json.dumps(payload, default=str))
    body = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(body) > DEFAULT_MAX_RESPONSE_BYTES:
        raise comparison.LearningComparisonError("investigation memory exceeds its size bound")
    return payload, body


def run_retained_investigation(inputs: comparison.ComparisonInputs) -> dict:
    """Save or verify one private result and return only its safe aggregate.

    Existing private report directory must be ready. Directory locking serializes
    cooperating writers; temp-file publication is atomic and never replaces an
    existing result. A failed or changed source cannot publish a stale finding.
    """
    directory = inputs.live().report_directory
    if not directory.is_absolute() or not directory.is_dir() or not _destination_ready(
        directory, private=True,
    ):
        raise comparison.LearningComparisonError("private investigation destination is not ready")
    descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        payload, body = _artifact(inputs)
        path = directory / ("retained-investigation-" + comparison._digest(body) + ".json")
        if path.exists() or path.is_symlink():
            if _read_private_bytes(path, "investigation memory") != body:
                raise comparison.LearningComparisonError("investigation memory differs")
            return payload["aggregate"]
        temporary = directory / (".investigation-" + uuid4().hex + ".tmp")
        try:
            _write_private_json(temporary, payload)
            if _artifact(inputs)[1] != body:
                raise comparison.LearningComparisonError("retained source changed during investigation")
            # link is atomic and fails if another writer created the destination.
            os.link(temporary, path, follow_symlinks=False)
            temporary.unlink()
            os.fsync(descriptor)
        finally:
            temporary.unlink(missing_ok=True)
        if _read_private_bytes(path, "investigation memory") != body:
            raise comparison.LearningComparisonError("investigation memory verification failed")
        return payload["aggregate"]
    finally:
        os.close(descriptor)
