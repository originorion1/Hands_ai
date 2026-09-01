"""Immutable, validated persistence contracts for historical observations."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from ..contracts import Evidence, EvidenceKind, Observation, ObservationMode
from ..discovery.planner import DiscoveryPlanError, validate_discovery_target

_FORMAT_VERSION = 1
_MAX_BATCH_OBSERVATIONS = 100
_MAX_SEQUENCE = 1_000_000_000
_BATCH_KEYS = frozenset(
    {"format_version", "tenant_id", "resource", "sequence", "created_at", "observations"}
)
_OBSERVATION_KEYS = frozenset({"observation_id", "mode", "evidence"})
_EVIDENCE_KEYS = frozenset(
    {
        "evidence_id",
        "kind",
        "source",
        "tenant_id",
        "observed_at",
        "confidence",
        "payload",
    }
)


class HistoricalEvidenceError(ValueError):
    """Raised when historical evidence is malformed or violates its contract."""


class HistoricalEvidenceConflictError(HistoricalEvidenceError):
    """Raised when a sequence already holds different immutable evidence."""


class HistoricalEvidenceSequenceError(HistoricalEvidenceError):
    """Raised when batch history is non-consecutive."""


class HistoricalEvidenceIntegrityError(HistoricalEvidenceError):
    """Raised when stored evidence cannot be authenticated and decoded."""


class HistoricalEvidenceStore(Protocol):
    """Append-only persistence port for immutable historical evidence."""

    def append(self, batch: HistoricalEvidenceBatch) -> None: ...

    def load_all(
        self, *, tenant_id: str, resource: str
    ) -> tuple[HistoricalEvidenceBatch, ...]: ...


@dataclass(frozen=True, slots=True)
class HistoricalEvidenceBatch:
    """One bounded, tenant-scoped collection of read-only API evidence."""

    tenant_id: str
    resource: str
    sequence: int
    created_at: datetime
    observations: tuple[Observation, ...]

    def __post_init__(self) -> None:
        _validate_tenant(self.tenant_id)
        _validate_resource_value(self.resource)

        if type(self.sequence) is not int or not 1 <= self.sequence <= _MAX_SEQUENCE:
            raise HistoricalEvidenceError(
                f"sequence must be between 1 and {_MAX_SEQUENCE}"
            )

        _validate_datetime(self.created_at, "created_at")

        if type(self.observations) is not tuple or not self.observations:
            raise HistoricalEvidenceError("observations must be a non-empty tuple")
        if len(self.observations) > _MAX_BATCH_OBSERVATIONS:
            raise HistoricalEvidenceError(
                f"observations exceed maximum of {_MAX_BATCH_OBSERVATIONS}"
            )

        observation_ids: set[UUID] = set()
        evidence_ids: set[UUID] = set()
        document_names: set[str] = set()

        for observation in self.observations:
            if not isinstance(observation, Observation):
                raise HistoricalEvidenceError("observations must contain Observation values")
            if not isinstance(observation.observation_id, UUID):
                raise HistoricalEvidenceError("observation_id must be a UUID")
            if observation.observation_id in observation_ids:
                raise HistoricalEvidenceError("duplicate observation UUID")
            observation_ids.add(observation.observation_id)

            if observation.mode is not ObservationMode.READ_ONLY:
                raise HistoricalEvidenceError("historical observations must be READ_ONLY")

            evidence = observation.evidence
            if not isinstance(evidence, Evidence):
                raise HistoricalEvidenceError("observation evidence must be Evidence")
            if not isinstance(evidence.evidence_id, UUID):
                raise HistoricalEvidenceError("evidence_id must be a UUID")
            if evidence.evidence_id in evidence_ids:
                raise HistoricalEvidenceError("duplicate evidence UUID")
            evidence_ids.add(evidence.evidence_id)

            if evidence.kind is not EvidenceKind.API:
                raise HistoricalEvidenceError("historical evidence must be API evidence")
            if evidence.tenant_id != self.tenant_id:
                raise HistoricalEvidenceError("evidence tenant must exactly match batch tenant")
            if not isinstance(evidence.source, str) or not evidence.source.strip():
                raise HistoricalEvidenceError("evidence source must be non-empty")
            _validate_datetime(evidence.observed_at, "evidence observed_at")
            _validate_confidence(evidence.confidence)

            payload = evidence.payload
            if not isinstance(payload, Mapping) or set(payload) != {"resource", "record"}:
                raise HistoricalEvidenceError(
                    "evidence payload must have exactly resource and record"
                )
            if payload["resource"] != self.resource:
                raise HistoricalEvidenceError("evidence resource must exactly match batch resource")
            record = payload["record"]
            if not isinstance(record, Mapping):
                raise HistoricalEvidenceError("evidence record must be an object")
            name = record.get("name")
            if not isinstance(name, str) or not name.strip():
                raise HistoricalEvidenceError("evidence record must have a non-empty name")
            if name in document_names:
                raise HistoricalEvidenceError("duplicate document identity")
            document_names.add(name)
            _validate_json_value(payload)


def historical_evidence_to_json(batch: HistoricalEvidenceBatch) -> str:
    """Serialize a batch into its canonical, versioned JSON representation."""

    if not isinstance(batch, HistoricalEvidenceBatch):
        raise HistoricalEvidenceError("batch must be HistoricalEvidenceBatch")

    payload = {
        "format_version": _FORMAT_VERSION,
        "tenant_id": batch.tenant_id,
        "resource": batch.resource,
        "sequence": batch.sequence,
        "created_at": batch.created_at.isoformat(),
        "observations": [_observation_to_data(item) for item in batch.observations],
    }
    try:
        return json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise HistoricalEvidenceError("historical evidence cannot be serialized") from exc


def historical_evidence_checksum(payload_json: str) -> str:
    """Return the SHA-256 checksum of canonical UTF-8 payload JSON."""

    if not isinstance(payload_json, str):
        raise HistoricalEvidenceError("payload_json must be a string")
    return hashlib.sha256(payload_json.encode("utf-8")).hexdigest()


def historical_evidence_from_json(payload_json: str) -> HistoricalEvidenceBatch:
    """Strictly decode and fully validate canonical historical evidence JSON."""

    if not isinstance(payload_json, str):
        raise HistoricalEvidenceError("payload_json must be a string")
    try:
        payload = json.loads(payload_json, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, TypeError) as exc:
        raise HistoricalEvidenceError("invalid historical evidence JSON") from exc

    _require_exact_keys(payload, _BATCH_KEYS, "batch")
    if payload["format_version"] != _FORMAT_VERSION:
        raise HistoricalEvidenceError("unsupported historical evidence format version")
    if not isinstance(payload["observations"], list):
        raise HistoricalEvidenceError("observations must be a JSON array")

    observations = tuple(_observation_from_data(item) for item in payload["observations"])
    return HistoricalEvidenceBatch(
        tenant_id=payload["tenant_id"],
        resource=payload["resource"],
        sequence=payload["sequence"],
        created_at=_datetime_from_data(payload["created_at"], "created_at"),
        observations=observations,
    )


def _observation_to_data(observation: Observation) -> dict[str, Any]:
    evidence = observation.evidence
    return {
        "observation_id": str(observation.observation_id),
        "mode": observation.mode.value,
        "evidence": {
            "evidence_id": str(evidence.evidence_id),
            "kind": evidence.kind.value,
            "source": evidence.source,
            "tenant_id": evidence.tenant_id,
            "observed_at": evidence.observed_at.isoformat(),
            "confidence": evidence.confidence,
            "payload": _normalize_json_value(evidence.payload),
        },
    }


def _observation_from_data(value: Any) -> Observation:
    _require_exact_keys(value, _OBSERVATION_KEYS, "observation")
    evidence_data = value["evidence"]
    _require_exact_keys(evidence_data, _EVIDENCE_KEYS, "evidence")
    _validate_json_value(evidence_data["payload"])
    try:
        observation_id = UUID(value["observation_id"])
        mode = ObservationMode(value["mode"])
        evidence_id = UUID(evidence_data["evidence_id"])
        kind = EvidenceKind(evidence_data["kind"])
    except (TypeError, ValueError, AttributeError) as exc:
        raise HistoricalEvidenceError("invalid observation or evidence identity") from exc
    return Observation(
        observation_id=observation_id,
        mode=mode,
        evidence=Evidence(
            evidence_id=evidence_id,
            kind=kind,
            source=evidence_data["source"],
            tenant_id=evidence_data["tenant_id"],
            observed_at=_datetime_from_data(evidence_data["observed_at"], "observed_at"),
            confidence=evidence_data["confidence"],
            payload=evidence_data["payload"],
        ),
    )


def _validate_tenant(tenant_id: str) -> None:
    if not isinstance(tenant_id, str) or not tenant_id.strip():
        raise HistoricalEvidenceError("tenant_id must be non-empty")
    if tenant_id != tenant_id.strip():
        raise HistoricalEvidenceError("tenant_id must not contain surrounding whitespace")


def _validate_resource_value(resource: str) -> None:
    try:
        validate_discovery_target(resource)
    except (DiscoveryPlanError, ValueError) as exc:
        raise HistoricalEvidenceError(str(exc)) from exc


def _validate_datetime(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise HistoricalEvidenceError(f"{name} must be timezone-aware")


def _validate_confidence(value: float | None) -> None:
    if value is None:
        return
    if type(value) not in {int, float} or isinstance(value, bool) or not math.isfinite(value):
        raise HistoricalEvidenceError("confidence must be finite numeric or None")


def _validate_json_value(value: Any) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise HistoricalEvidenceError("payload must not contain non-finite numbers")
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise HistoricalEvidenceError("payload object keys must be strings")
            _validate_json_value(item)
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(item)
        return
    raise HistoricalEvidenceError("payload must be JSON-compatible")


def _normalize_json_value(value: Any) -> Any:
    """Convert accepted JSON-like mappings into plain JSON containers."""

    _validate_json_value(value)
    if isinstance(value, Mapping):
        return {key: _normalize_json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_json_value(item) for item in value]
    return value


def _require_exact_keys(value: Any, expected: frozenset[str], name: str) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        raise HistoricalEvidenceError(f"{name} contains unsupported or missing fields")


def _datetime_from_data(value: Any, name: str) -> datetime:
    if not isinstance(value, str):
        raise HistoricalEvidenceError(f"{name} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise HistoricalEvidenceError(f"{name} must be an ISO-8601 timestamp") from exc
    _validate_datetime(parsed, name)
    return parsed


def _reject_json_constant(value: str) -> Any:
    raise HistoricalEvidenceError(f"non-finite JSON constant is not allowed: {value}")
