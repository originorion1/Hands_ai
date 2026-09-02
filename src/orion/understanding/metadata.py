"""Turn read-only metadata evidence into tenant-scoped structural understanding.

Metadata understanding is descriptive only. It does not create knowledge,
authorization, recommendations, or execution authority.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from uuid import NAMESPACE_URL, UUID, uuid5

from ..contracts import EvidenceKind, Observation, ObservationMode
from .graph import (
    GraphNode,
    GraphRelationship,
    GraphStatus,
    GraphStore,
    NodeType,
    RelationshipType,
)


class MetadataUnderstandingError(ValueError):
    """Raised when metadata evidence cannot be safely understood."""


@dataclass(frozen=True, slots=True)
class StructuralField:
    doctype: str
    fieldname: str
    fieldtype: str
    label: str | None
    options: str | None
    required: bool
    read_only: bool
    hidden: bool
    unique: bool


@dataclass(frozen=True, slots=True)
class StructuralEntity:
    doctype: str
    module: str | None
    is_submittable: bool
    is_child_table: bool
    is_single: bool
    fields: tuple[StructuralField, ...]
    provenance_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class MetadataUnderstanding:
    tenant_id: str
    entities: tuple[StructuralEntity, ...]


def relationship_target(field: StructuralField) -> str | None:
    """Return a relationship target only for relationship field types.

    ``options`` is also used by selectable/enumerated fields and may contain
    arbitrary display text.  It is never a relationship signal by itself.
    """
    if not isinstance(field, StructuralField):
        raise TypeError("field must be StructuralField")
    if field.fieldtype not in {"Link", "Table", "Table MultiSelect"}:
        return None
    if not isinstance(field.options, str) or not field.options or field.options != field.options.strip() or any(ord(char) < 32 for char in field.options) or "\n" in field.options or "\r" in field.options:
        return None
    return field.options


@dataclass(frozen=True, slots=True)
class MetadataProjectionReport:
    added_nodes: int
    added_relationships: int
    node_ids: tuple[UUID, ...]
    relationship_ids: tuple[UUID, ...]


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


def _flag(value: object) -> bool:
    if value in (None, False, 0, "", "0"):
        return False
    if value in (True, 1, "1"):
        return True
    raise MetadataUnderstandingError(
        f"unsupported metadata boolean representation: {value!r}"
    )


def _extract_docs(metadata: Mapping[str, object]) -> list[Mapping[str, object]]:
    docs = metadata.get("docs")

    if docs is None:
        message = metadata.get("message")
        if isinstance(message, Mapping):
            docs = message.get("docs")

    if not isinstance(docs, list):
        raise MetadataUnderstandingError(
            "metadata response must contain a docs list"
        )

    if any(not isinstance(item, Mapping) for item in docs):
        raise MetadataUnderstandingError(
            "metadata docs must contain JSON objects"
        )

    return list(docs)


def _parse_fields(
    doctype: str,
    raw_fields: object,
) -> tuple[StructuralField, ...]:
    if raw_fields is None:
        return ()

    if not isinstance(raw_fields, list):
        raise MetadataUnderstandingError(
            f"fields must be a list for {doctype}"
        )

    fields: list[StructuralField] = []
    seen: set[str] = set()

    for raw_field in raw_fields:
        if not isinstance(raw_field, Mapping):
            raise MetadataUnderstandingError(
                f"field metadata must be an object for {doctype}"
            )

        fieldname = raw_field.get("fieldname")
        fieldtype = raw_field.get("fieldtype")

        # Layout-only metadata can legitimately lack a stable field name.
        if not isinstance(fieldname, str) or not fieldname:
            continue

        if not isinstance(fieldtype, str) or not fieldtype:
            raise MetadataUnderstandingError(
                f"field {doctype}.{fieldname} has no field type"
            )

        if fieldname in seen:
            raise MetadataUnderstandingError(
                f"duplicate field {doctype}.{fieldname}"
            )
        seen.add(fieldname)

        fields.append(
            StructuralField(
                doctype=doctype,
                fieldname=fieldname,
                fieldtype=fieldtype,
                label=_optional_string(raw_field.get("label")),
                options=_optional_string(raw_field.get("options")),
                required=_flag(raw_field.get("reqd")),
                read_only=_flag(raw_field.get("read_only")),
                hidden=_flag(raw_field.get("hidden")),
                unique=_flag(raw_field.get("unique")),
            )
        )

    return tuple(fields)


def _parse_entity(
    raw_doc: Mapping[str, object],
    *,
    evidence_id: UUID,
) -> StructuralEntity | None:
    name = raw_doc.get("name")

    if not isinstance(name, str) or not name:
        return None

    return StructuralEntity(
        doctype=name,
        module=_optional_string(raw_doc.get("module")),
        is_submittable=_flag(raw_doc.get("is_submittable")),
        is_child_table=_flag(raw_doc.get("istable")),
        is_single=_flag(raw_doc.get("issingle")),
        fields=_parse_fields(name, raw_doc.get("fields")),
        provenance_ids=(evidence_id,),
    )


def _entity_signature(entity: StructuralEntity) -> tuple[object, ...]:
    return (
        entity.doctype,
        entity.module,
        entity.is_submittable,
        entity.is_child_table,
        entity.is_single,
        entity.fields,
    )


def build_metadata_understanding(
    observations: tuple[Observation, ...],
    *,
    tenant_id: str,
    allowed_doctypes: frozenset[str] | None = None,
) -> MetadataUnderstanding:
    """Normalize metadata evidence without mutating any ORION store.

    When ``allowed_doctypes`` is supplied, raw metadata bundles remain valid
    evidence but only explicitly allowed DocTypes may enter structural
    understanding.
    """

    if not isinstance(tenant_id, str) or not tenant_id.strip():
        raise MetadataUnderstandingError("tenant_id must be non-empty")

    if (
        allowed_doctypes is not None
        and any(
            not isinstance(doctype, str) or not doctype.strip()
            for doctype in allowed_doctypes
        )
    ):
        raise MetadataUnderstandingError(
            "allowed_doctypes must contain non-empty strings"
        )

    entities: dict[str, StructuralEntity] = {}

    for observation in observations:
        evidence = observation.evidence

        if observation.mode is not ObservationMode.READ_ONLY:
            raise MetadataUnderstandingError(
                "metadata understanding accepts READ_ONLY observations only"
            )

        if evidence.kind is not EvidenceKind.METADATA:
            raise MetadataUnderstandingError(
                "metadata understanding accepts METADATA evidence only"
            )

        if evidence.tenant_id != tenant_id:
            raise MetadataUnderstandingError(
                "metadata evidence crosses tenant boundary"
            )

        requested_doctype = evidence.payload.get("doctype")
        metadata = evidence.payload.get("metadata")

        if not isinstance(requested_doctype, str) or not requested_doctype:
            raise MetadataUnderstandingError(
                "metadata evidence requires requested doctype"
            )

        if (
            allowed_doctypes is not None
            and requested_doctype not in allowed_doctypes
        ):
            raise MetadataUnderstandingError(
                "requested DocType outside allowed structural scope: "
                f"{requested_doctype}"
            )

        if not isinstance(metadata, Mapping):
            raise MetadataUnderstandingError(
                "metadata evidence requires metadata object"
            )

        docs = _extract_docs(metadata)
        requested_found = False

        for raw_doc in docs:
            entity = _parse_entity(
                raw_doc,
                evidence_id=evidence.evidence_id,
            )

            if entity is None:
                continue

            if entity.doctype == requested_doctype:
                requested_found = True

            if (
                allowed_doctypes is not None
                and entity.doctype not in allowed_doctypes
            ):
                continue

            existing = entities.get(entity.doctype)

            if existing is None:
                entities[entity.doctype] = entity
                continue

            if _entity_signature(existing) != _entity_signature(entity):
                raise MetadataUnderstandingError(
                    f"conflicting metadata for {entity.doctype}"
                )

            merged_provenance = tuple(
                dict.fromkeys(
                    existing.provenance_ids + entity.provenance_ids
                )
            )

            entities[entity.doctype] = replace(
                existing,
                provenance_ids=merged_provenance,
            )

        if not requested_found:
            raise MetadataUnderstandingError(
                f"requested DocType missing from metadata bundle: "
                f"{requested_doctype}"
            )

    return MetadataUnderstanding(
        tenant_id=tenant_id,
        entities=tuple(
            entities[name]
            for name in sorted(entities)
        ),
    )


def merge_metadata_understandings(
    current: MetadataUnderstanding,
    additional: MetadataUnderstanding,
) -> MetadataUnderstanding:
    """Merge compatible tenant-scoped structural observations.

    Conflicting structural snapshots fail closed. Existing structure is never
    silently overwritten by later metadata.
    """

    if (
        not isinstance(current.tenant_id, str)
        or not current.tenant_id.strip()
        or not isinstance(additional.tenant_id, str)
        or not additional.tenant_id.strip()
    ):
        raise MetadataUnderstandingError(
            "metadata understanding requires non-empty tenant_id"
        )

    if current.tenant_id != additional.tenant_id:
        raise MetadataUnderstandingError(
            "metadata understanding merge crosses tenant boundary"
        )

    entities = {
        entity.doctype: entity
        for entity in current.entities
    }

    for entity in additional.entities:
        existing = entities.get(entity.doctype)

        if existing is None:
            entities[entity.doctype] = entity
            continue

        if _entity_signature(existing) != _entity_signature(entity):
            raise MetadataUnderstandingError(
                f"conflicting metadata for {entity.doctype}"
            )

        merged_provenance = tuple(
            dict.fromkeys(
                existing.provenance_ids + entity.provenance_ids
            )
        )

        entities[entity.doctype] = replace(
            existing,
            provenance_ids=merged_provenance,
        )

    return MetadataUnderstanding(
        tenant_id=current.tenant_id,
        entities=tuple(
            entities[name]
            for name in sorted(entities)
        ),
    )


def _stable_id(tenant_id: str, key: str) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"orion://tenant/{tenant_id}/structure/{key}",
    )


def _schema_key(doctype: str) -> str:
    return f"schema:doctype:{doctype}"


def _field_key(doctype: str, fieldname: str) -> str:
    return f"schema:doctype:{doctype}:field:{fieldname}"


def project_metadata_understanding(
    graph: GraphStore,
    understanding: MetadataUnderstanding,
) -> MetadataProjectionReport:
    """Project validated structural understanding into the system graph."""

    tenant_id = understanding.tenant_id

    nodes: dict[UUID, GraphNode] = {}
    relationships: dict[UUID, GraphRelationship] = {}

    schema_ids = {
        entity.doctype: _stable_id(
            tenant_id,
            _schema_key(entity.doctype),
        )
        for entity in understanding.entities
    }

    for entity in understanding.entities:
        schema_id = schema_ids[entity.doctype]

        nodes[schema_id] = GraphNode(
            node_id=schema_id,
            node_type=NodeType.COMPONENT,
            tenant_id=tenant_id,
            key=_schema_key(entity.doctype),
            attributes={
                "structure_kind": "doctype",
                "doctype": entity.doctype,
                "module": entity.module,
                "is_submittable": entity.is_submittable,
                "is_child_table": entity.is_child_table,
                "is_single": entity.is_single,
                "field_count": len(entity.fields),
            },
            status=GraphStatus.OBSERVED,
            provenance_ids=entity.provenance_ids,
        )

        for field in entity.fields:
            field_key = _field_key(
                entity.doctype,
                field.fieldname,
            )
            field_id = _stable_id(tenant_id, field_key)

            nodes[field_id] = GraphNode(
                node_id=field_id,
                node_type=NodeType.ATTRIBUTE,
                tenant_id=tenant_id,
                key=field_key,
                attributes={
                    "structure_kind": "field",
                    "doctype": field.doctype,
                    "fieldname": field.fieldname,
                    "fieldtype": field.fieldtype,
                    "label": field.label,
                    "options": field.options,
                    "required": field.required,
                    "read_only": field.read_only,
                    "hidden": field.hidden,
                    "unique": field.unique,
                },
                status=GraphStatus.OBSERVED,
                provenance_ids=entity.provenance_ids,
            )

            has_attribute_id = _stable_id(
                tenant_id,
                f"relationship:{schema_id}:has_attribute:{field_id}",
            )

            relationships[has_attribute_id] = GraphRelationship(
                relationship_id=has_attribute_id,
                relationship_type=RelationshipType.HAS_ATTRIBUTE,
                source_id=schema_id,
                target_id=field_id,
                tenant_id=tenant_id,
                status=GraphStatus.OBSERVED,
                provenance_ids=entity.provenance_ids,
            )

            target = relationship_target(field)
            if target in schema_ids:
                target_id = schema_ids[target]

                relates_id = _stable_id(
                    tenant_id,
                    f"relationship:{field_id}:relates_to:{target_id}",
                )

                relationships[relates_id] = GraphRelationship(
                    relationship_id=relates_id,
                    relationship_type=RelationshipType.RELATES_TO,
                    source_id=field_id,
                    target_id=target_id,
                    tenant_id=tenant_id,
                    status=GraphStatus.OBSERVED,
                    provenance_ids=entity.provenance_ids,
                )

    added_nodes = 0

    for node in nodes.values():
        existing = graph.get_node(
            node.node_id,
            tenant_id=tenant_id,
        )

        if existing is None:
            graph.add_node(node)
            added_nodes += 1

    added_relationships = 0

    for relationship in relationships.values():
        existing = graph.get_relationship(
            relationship.relationship_id,
            tenant_id=tenant_id,
        )

        if existing is None:
            graph.add_relationship(relationship)
            added_relationships += 1

    return MetadataProjectionReport(
        added_nodes=added_nodes,
        added_relationships=added_relationships,
        node_ids=tuple(nodes),
        relationship_ids=tuple(relationships),
    )
