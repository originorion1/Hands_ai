"""Durable-state contract for ORION governed study.

A checkpoint records learned study state only. It deliberately contains no
authorization, credentials, execution permission, or ERP-specific capability.

Loading a checkpoint never restores authority. Fresh authorization must always
be supplied separately by the caller before further study.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from ..understanding.metadata import (
    MetadataUnderstanding,
    StructuralEntity,
    StructuralField,
)
from .planner import validate_discovery_target

CHECKPOINT_FORMAT_VERSION = 1


class StudyCheckpointError(ValueError):
    """Base class for invalid checkpoint state."""


class StudyCheckpointIntegrityError(StudyCheckpointError):
    """Raised when persisted checkpoint integrity cannot be verified."""


class StudyCheckpointConflictError(StudyCheckpointError):
    """Raised when immutable checkpoint history would be overwritten."""


class StudyCheckpointSequenceError(StudyCheckpointError):
    """Raised when checkpoint history contains a sequence gap or rewind."""


@dataclass(frozen=True, slots=True)
class StudyCheckpoint:
    """Immutable tenant-scoped snapshot of autonomous study progress."""

    tenant_id: str
    sequence: int
    created_at: datetime
    understanding: MetadataUnderstanding
    sampled_records: frozenset[str]
    metadata_targets_studied: tuple[str, ...]
    record_targets_sampled: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.tenant_id, str) or not self.tenant_id.strip():
            raise StudyCheckpointError("tenant_id must be non-empty")

        if (
            type(self.sequence) is not int
            or not 1 <= self.sequence <= 1_000_000_000
        ):
            raise StudyCheckpointError(
                "sequence must be between 1 and 1000000000"
            )

        if (
            not isinstance(self.created_at, datetime)
            or self.created_at.utcoffset() is None
        ):
            raise StudyCheckpointError(
                "created_at must be timezone-aware"
            )

        if self.understanding.tenant_id != self.tenant_id:
            raise StudyCheckpointError(
                "checkpoint understanding crosses tenant boundary"
            )

        if (
            len(set(self.metadata_targets_studied))
            != len(self.metadata_targets_studied)
        ):
            raise StudyCheckpointError(
                "metadata_targets_studied must be unique"
            )

        if (
            len(set(self.record_targets_sampled))
            != len(self.record_targets_sampled)
        ):
            raise StudyCheckpointError(
                "record_targets_sampled must be unique"
            )

        for target in (
            set(self.sampled_records)
            | set(self.metadata_targets_studied)
            | set(self.record_targets_sampled)
        ):
            validate_discovery_target(target)

        understood = {
            entity.doctype
            for entity in self.understanding.entities
        }

        if not set(self.sampled_records).issubset(understood):
            raise StudyCheckpointError(
                "sampled record structure must already be understood"
            )

        if not set(self.metadata_targets_studied).issubset(understood):
            raise StudyCheckpointError(
                "studied metadata target must exist in understanding"
            )

        if not set(self.record_targets_sampled).issubset(
            self.sampled_records
        ):
            raise StudyCheckpointError(
                "record_targets_sampled must be included in sampled_records"
            )


class StudyCheckpointStore(Protocol):
    """Persistence port for immutable study checkpoints."""

    def append(self, checkpoint: StudyCheckpoint) -> None: ...

    def load_latest(
        self,
        *,
        tenant_id: str,
    ) -> StudyCheckpoint | None: ...


def _field_payload(field: StructuralField) -> dict[str, object]:
    return {
        "doctype": field.doctype,
        "fieldname": field.fieldname,
        "fieldtype": field.fieldtype,
        "label": field.label,
        "options": field.options,
        "required": field.required,
        "read_only": field.read_only,
        "hidden": field.hidden,
        "unique": field.unique,
    }


def _entity_payload(entity: StructuralEntity) -> dict[str, object]:
    return {
        "doctype": entity.doctype,
        "module": entity.module,
        "is_submittable": entity.is_submittable,
        "is_child_table": entity.is_child_table,
        "is_single": entity.is_single,
        "fields": [
            _field_payload(field)
            for field in entity.fields
        ],
        "provenance_ids": [
            str(evidence_id)
            for evidence_id in entity.provenance_ids
        ],
    }


def checkpoint_to_json(checkpoint: StudyCheckpoint) -> str:
    """Serialize one checkpoint using deterministic canonical JSON."""

    payload = {
        "format_version": CHECKPOINT_FORMAT_VERSION,
        "tenant_id": checkpoint.tenant_id,
        "sequence": checkpoint.sequence,
        "created_at": checkpoint.created_at.isoformat(),
        "understanding": {
            "tenant_id": checkpoint.understanding.tenant_id,
            "entities": [
                _entity_payload(entity)
                for entity in checkpoint.understanding.entities
            ],
        },
        "sampled_records": sorted(checkpoint.sampled_records),
        "metadata_targets_studied": list(
            checkpoint.metadata_targets_studied
        ),
        "record_targets_sampled": list(
            checkpoint.record_targets_sampled
        ),
    }

    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def checkpoint_checksum(payload_json: str) -> str:
    """Return deterministic SHA-256 corruption-detection checksum."""

    if not isinstance(payload_json, str):
        raise StudyCheckpointError(
            "checkpoint payload must be JSON text"
        )

    return hashlib.sha256(
        payload_json.encode("utf-8")
    ).hexdigest()


def _require_mapping(
    value: object,
    *,
    name: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise StudyCheckpointError(f"{name} must be an object")
    return value


def _require_exact_keys(
    value: Mapping[str, Any],
    *,
    keys: set[str],
    name: str,
) -> None:
    if set(value) != keys:
        raise StudyCheckpointError(
            f"{name} contains unsupported or missing fields"
        )


def _require_string(
    value: object,
    *,
    name: str,
) -> str:
    if not isinstance(value, str) or not value:
        raise StudyCheckpointError(f"{name} must be non-empty text")
    return value


def _optional_string(
    value: object,
    *,
    name: str,
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise StudyCheckpointError(
            f"{name} must be text or null"
        )
    return value


def _require_bool(
    value: object,
    *,
    name: str,
) -> bool:
    if type(value) is not bool:
        raise StudyCheckpointError(f"{name} must be boolean")
    return value


def _decode_field(
    raw: object,
    *,
    entity_doctype: str,
) -> StructuralField:
    value = _require_mapping(raw, name="field")

    _require_exact_keys(
        value,
        keys={
            "doctype",
            "fieldname",
            "fieldtype",
            "label",
            "options",
            "required",
            "read_only",
            "hidden",
            "unique",
        },
        name="field",
    )

    doctype = _require_string(
        value["doctype"],
        name="field.doctype",
    )

    if doctype != entity_doctype:
        raise StudyCheckpointError(
            "field doctype does not match structural entity"
        )

    return StructuralField(
        doctype=doctype,
        fieldname=_require_string(
            value["fieldname"],
            name="field.fieldname",
        ),
        fieldtype=_require_string(
            value["fieldtype"],
            name="field.fieldtype",
        ),
        label=_optional_string(
            value["label"],
            name="field.label",
        ),
        options=_optional_string(
            value["options"],
            name="field.options",
        ),
        required=_require_bool(
            value["required"],
            name="field.required",
        ),
        read_only=_require_bool(
            value["read_only"],
            name="field.read_only",
        ),
        hidden=_require_bool(
            value["hidden"],
            name="field.hidden",
        ),
        unique=_require_bool(
            value["unique"],
            name="field.unique",
        ),
    )


def _decode_entity(raw: object) -> StructuralEntity:
    value = _require_mapping(raw, name="entity")

    _require_exact_keys(
        value,
        keys={
            "doctype",
            "module",
            "is_submittable",
            "is_child_table",
            "is_single",
            "fields",
            "provenance_ids",
        },
        name="entity",
    )

    doctype = _require_string(
        value["doctype"],
        name="entity.doctype",
    )

    raw_fields = value["fields"]
    if not isinstance(raw_fields, list):
        raise StudyCheckpointError("entity.fields must be a list")

    fields = tuple(
        _decode_field(
            field,
            entity_doctype=doctype,
        )
        for field in raw_fields
    )

    if len({field.fieldname for field in fields}) != len(fields):
        raise StudyCheckpointError(
            "checkpoint entity contains duplicate fields"
        )

    raw_provenance = value["provenance_ids"]
    if not isinstance(raw_provenance, list) or not raw_provenance:
        raise StudyCheckpointError(
            "entity.provenance_ids must be a non-empty list"
        )

    try:
        provenance_ids = tuple(
            UUID(_require_string(item, name="provenance id"))
            for item in raw_provenance
        )
    except ValueError as exc:
        raise StudyCheckpointError(
            "checkpoint contains invalid provenance UUID"
        ) from exc

    return StructuralEntity(
        doctype=doctype,
        module=_optional_string(
            value["module"],
            name="entity.module",
        ),
        is_submittable=_require_bool(
            value["is_submittable"],
            name="entity.is_submittable",
        ),
        is_child_table=_require_bool(
            value["is_child_table"],
            name="entity.is_child_table",
        ),
        is_single=_require_bool(
            value["is_single"],
            name="entity.is_single",
        ),
        fields=fields,
        provenance_ids=provenance_ids,
    )


def _decode_string_list(
    value: object,
    *,
    name: str,
) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise StudyCheckpointError(f"{name} must be a list")

    return tuple(
        _require_string(item, name=name)
        for item in value
    )


def checkpoint_from_json(payload_json: str) -> StudyCheckpoint:
    """Decode and validate canonical checkpoint state."""

    if not isinstance(payload_json, str) or not payload_json:
        raise StudyCheckpointError(
            "checkpoint payload must be non-empty JSON text"
        )

    try:
        raw: Any = json.loads(payload_json)
    except json.JSONDecodeError as exc:
        raise StudyCheckpointError(
            "checkpoint payload is invalid JSON"
        ) from exc

    value = _require_mapping(raw, name="checkpoint")

    _require_exact_keys(
        value,
        keys={
            "format_version",
            "tenant_id",
            "sequence",
            "created_at",
            "understanding",
            "sampled_records",
            "metadata_targets_studied",
            "record_targets_sampled",
        },
        name="checkpoint",
    )

    if value["format_version"] != CHECKPOINT_FORMAT_VERSION:
        raise StudyCheckpointError(
            "unsupported checkpoint format version"
        )

    tenant_id = _require_string(
        value["tenant_id"],
        name="checkpoint.tenant_id",
    )

    sequence = value["sequence"]
    if type(sequence) is not int:
        raise StudyCheckpointError(
            "checkpoint.sequence must be an integer"
        )

    created_at_text = _require_string(
        value["created_at"],
        name="checkpoint.created_at",
    )

    try:
        created_at = datetime.fromisoformat(created_at_text)
    except ValueError as exc:
        raise StudyCheckpointError(
            "checkpoint.created_at is invalid"
        ) from exc

    understanding_raw = _require_mapping(
        value["understanding"],
        name="understanding",
    )

    _require_exact_keys(
        understanding_raw,
        keys={"tenant_id", "entities"},
        name="understanding",
    )

    understanding_tenant = _require_string(
        understanding_raw["tenant_id"],
        name="understanding.tenant_id",
    )

    raw_entities = understanding_raw["entities"]
    if not isinstance(raw_entities, list):
        raise StudyCheckpointError(
            "understanding.entities must be a list"
        )

    entities = tuple(
        _decode_entity(entity)
        for entity in raw_entities
    )

    if len({entity.doctype for entity in entities}) != len(entities):
        raise StudyCheckpointError(
            "checkpoint contains duplicate structural entities"
        )

    metadata_targets = _decode_string_list(
        value["metadata_targets_studied"],
        name="metadata_targets_studied",
    )

    record_targets = _decode_string_list(
        value["record_targets_sampled"],
        name="record_targets_sampled",
    )

    sampled_records = frozenset(
        _decode_string_list(
            value["sampled_records"],
            name="sampled_records",
        )
    )

    return StudyCheckpoint(
        tenant_id=tenant_id,
        sequence=sequence,
        created_at=created_at,
        understanding=MetadataUnderstanding(
            tenant_id=understanding_tenant,
            entities=entities,
        ),
        sampled_records=sampled_records,
        metadata_targets_studied=metadata_targets,
        record_targets_sampled=record_targets,
    )
