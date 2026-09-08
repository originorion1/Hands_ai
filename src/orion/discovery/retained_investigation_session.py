"""Offline, immutable investigation memory over validated retained snapshots.

This adapter never acquires ERP data. Reload recomputes the descriptive result
against the exact retained snapshot; it does not elevate a stored finding to a
business verdict. New snapshots have new artifacts; older memory is preserved.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import stat
from dataclasses import asdict
from uuid import uuid4

from ..learning.investigation_disposition import (
    disposition_from_finding,
    disposition_from_json,
    disposition_to_json,
)
from ..learning.retained_quality_investigation import investigate_retained_missing_values
from . import erpnext_learning_comparison as comparison
from .erpnext_adapter import DEFAULT_MAX_RESPONSE_BYTES
from .erpnext_live_session import _destination_ready, _study_authorization
from .erpnext_metadata_preflight import _write_private_json
from .erpnext_metadata_refresh import _read_private_bytes


def _recover_published_temporary(path, body):
    """Complete only our verified link/unlink publication after process death."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if info.st_nlink != 2:
            return
        if (
            not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size != len(body)
        ):
            raise comparison.LearningComparisonError("interrupted investigation memory is invalid")
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            if stream.read(len(body) + 1) != body:
                raise comparison.LearningComparisonError("interrupted investigation memory differs")
        candidates = []
        for temporary in path.parent.glob(".investigation-*.tmp"):
            if re.fullmatch(r"\.investigation-[0-9a-f]{32}\.tmp", temporary.name):
                candidate = temporary.lstat()
                if (candidate.st_dev, candidate.st_ino) == (info.st_dev, info.st_ino):
                    candidates.append(temporary)
        if len(candidates) != 1:
            raise comparison.LearningComparisonError("interrupted publication link is not recognized")
        candidates[0].unlink()
    finally:
        os.close(descriptor)


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


def _run_memory(inputs, factory, prefix):
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
        payload, body = factory(inputs)
        path = directory / (prefix + comparison._digest(body) + ".json")
        if path.exists() or path.is_symlink():
            _recover_published_temporary(path, body)
            os.fsync(descriptor)
            if _read_private_bytes(path, "investigation memory") != body:
                raise comparison.LearningComparisonError("investigation memory differs")
            return payload["aggregate"]
        temporary = directory / (".investigation-" + uuid4().hex + ".tmp")
        try:
            _write_private_json(temporary, payload)
            if factory(inputs)[1] != body:
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


def run_retained_investigation(inputs: comparison.ComparisonInputs) -> dict:
    """Preserve the original idempotent snapshot-investigation API."""
    return _run_memory(inputs, _artifact, "retained-investigation-")


def _disposition_prefix(inputs):
    live = inputs.live()
    return "retained-disposition-" + comparison._json_digest({
        "tenant": live.tenant_id,
        "companies": live.company_authorization.companies,
        "scopes": [(scope.entity, scope.fields) for scope in live.reviewed_scopes],
        "authorization": inputs.authorization_reference,
    }) + "-"


def _planned_artifact(inputs):
    live = inputs.live()
    if len(live.company_authorization.companies) != 1:
        raise comparison.LearningComparisonError("investigation requires one exact company")
    dispositions = []
    prefix = _disposition_prefix(inputs)
    for path in sorted(live.report_directory.glob(prefix + "*.json")):
        if path.lstat().st_nlink == 2:
            # Recover only a hash-bound completed publication, under the same lock.
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as stream:
                info = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                    or stat.S_IMODE(info.st_mode) != 0o600
                ):
                    raise comparison.LearningComparisonError("disposition is not private")
                body = stream.read(DEFAULT_MAX_RESPONSE_BYTES + 1)
            if len(body) > DEFAULT_MAX_RESPONSE_BYTES or path.name != (
                prefix + comparison._digest(body) + ".json"
            ):
                raise comparison.LearningComparisonError("disposition recovery binding differs")
            _recover_published_temporary(path, body)
        body = _read_private_bytes(path, "investigation disposition")
        if path.name != prefix + comparison._digest(body) + ".json":
            raise comparison.LearningComparisonError("investigation disposition digest differs")
        payload = json.loads(body, object_pairs_hook=comparison._unique_json_object)
        if type(payload) is not dict or set(payload) != {"schema", "disposition", "aggregate"}:
            raise comparison.LearningComparisonError("investigation disposition artifact differs")
        if type(payload["schema"]) is not int or payload["schema"] != 1:
            raise comparison.LearningComparisonError("investigation disposition version differs")
        if payload["disposition"] is not None:
            dispositions.append(disposition_from_json(json.dumps(payload["disposition"])))
    _, _, batches, understanding = comparison._state(inputs)
    result = investigate_retained_missing_values(
        understanding, batches, authorization=_study_authorization(live),
        company=live.company_authorization.companies[0], dispositions=tuple(dispositions),
    )
    payload = {
        "schema": 1,
        "disposition": json.loads(disposition_to_json(disposition_from_finding(result.finding)))
        if result.finding else None,
        "aggregate": result.aggregate(),
    }
    body = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(body) > DEFAULT_MAX_RESPONSE_BYTES:
        raise comparison.LearningComparisonError("investigation disposition exceeds size bound")
    return payload, body


def run_retained_investigation_planner(inputs: comparison.ComparisonInputs) -> dict:
    """Remember one inconclusive target, then redirect the next offline call.

    Dispositions are separate from observations. Only unchanged relevant target
    evidence is deferred; fresh evidence or target structure reopens the question.
    """
    return _run_memory(inputs, _planned_artifact, _disposition_prefix(inputs))
