"""Immutable profile-aware evidence contract for richer customer captures."""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from ..contracts import Evidence, EvidenceKind, Observation, ObservationMode
from .evidence import HistoricalEvidenceError


@dataclass(frozen=True, slots=True)
class ProfiledEvidenceBatch:
    tenant_id: str
    resource: str
    profile_id: str
    sequence: int
    created_at: datetime
    observations: tuple[Observation, ...]

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) and value.strip() == value and value for value in (self.tenant_id, self.resource, self.profile_id)):
            raise HistoricalEvidenceError("profiled evidence scope is invalid")
        if type(self.sequence) is not int or self.sequence < 1 or not isinstance(self.created_at, datetime) or self.created_at.tzinfo is None:
            raise HistoricalEvidenceError("profiled evidence envelope is invalid")
        if not isinstance(self.observations, tuple) or not self.observations or len(self.observations) > 100:
            raise HistoricalEvidenceError("profiled observations must contain 1..100 values")
        names: set[str] = set()
        observation_ids: set[UUID] = set()
        evidence_ids: set[UUID] = set()
        for observation in self.observations:
            if observation.mode is not ObservationMode.READ_ONLY or observation.evidence.kind is not EvidenceKind.API:
                raise HistoricalEvidenceError("profiled observations must be read-only API evidence")
            if observation.evidence.tenant_id != self.tenant_id:
                raise HistoricalEvidenceError("profiled evidence tenant mismatch")
            if observation.observation_id in observation_ids or observation.evidence.evidence_id in evidence_ids:
                raise HistoricalEvidenceError("duplicate profiled observation/evidence identity")
            payload = observation.evidence.payload
            if set(payload) != {"resource", "profile_id", "record"} or payload["resource"] != self.resource or payload["profile_id"] != self.profile_id or not isinstance(payload["record"], dict):
                raise HistoricalEvidenceError("profiled payload contract is invalid")
            name = payload["record"].get("name")
            if not isinstance(name, str) or not name.strip() or name in names:
                raise HistoricalEvidenceError("duplicate or invalid profiled document identity")
            names.add(name)
            observation_ids.add(observation.observation_id)
            evidence_ids.add(observation.evidence.evidence_id)
            _validate_json(payload)


def profiled_evidence_to_json(batch: ProfiledEvidenceBatch) -> str:
    payload = {
        "profile_id": batch.profile_id,
        "tenant_id": batch.tenant_id,
        "resource": batch.resource,
        "sequence": batch.sequence,
        "created_at": batch.created_at.isoformat(),
        "observations": [
            {
                "observation_id": str(item.observation_id),
                "mode": item.mode.value,
                "evidence": {
                    "evidence_id": str(item.evidence.evidence_id),
                    "kind": item.evidence.kind.value,
                    "source": item.evidence.source,
                    "tenant_id": item.evidence.tenant_id,
                    "observed_at": item.evidence.observed_at.isoformat(),
                    "confidence": item.evidence.confidence,
                    "payload": item.evidence.payload,
                },
            }
            for item in batch.observations
        ],
    }
    return json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)


def profiled_evidence_checksum(payload_json: str) -> str:
    return hashlib.sha256(payload_json.encode()).hexdigest()


def profiled_evidence_from_json(payload_json: str) -> ProfiledEvidenceBatch:
    try:
        data = json.loads(payload_json)
        observations = tuple(
            Observation(
                observation_id=UUID(item["observation_id"]),
                mode=ObservationMode(item["mode"]),
                evidence=Evidence(
                    evidence_id=UUID(item["evidence"]["evidence_id"]),
                    kind=EvidenceKind(item["evidence"]["kind"]),
                    source=item["evidence"]["source"],
                    tenant_id=item["evidence"]["tenant_id"],
                    observed_at=datetime.fromisoformat(item["evidence"]["observed_at"]),
                    confidence=item["evidence"]["confidence"],
                    payload=item["evidence"]["payload"],
                ),
            )
            for item in data["observations"]
        )
        return ProfiledEvidenceBatch(data["tenant_id"], data["resource"], data["profile_id"], data["sequence"], datetime.fromisoformat(data["created_at"]), observations)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HistoricalEvidenceError("invalid profiled evidence payload") from exc


def _validate_json(value: Any) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise HistoricalEvidenceError("profiled payload contains non-finite number")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise HistoricalEvidenceError("profiled payload key is not a string")
            _validate_json(item)
        return
    if isinstance(value, list):
        for item in value:
            _validate_json(item)
        return
    raise HistoricalEvidenceError("profiled payload is not JSON-compatible")
