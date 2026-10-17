"""Reviewed, data-only connector catalog for future customer onboarding.

This module has no network, credential, or execution path. The schema bytes
must come from a separately reviewed package artifact and match its reviewed
digest. The caller is responsible for that artifact's custody.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

from orion_connector_sdk import ReviewedConnector, discover

_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_LABEL = re.compile(r"[A-Za-z][A-Za-z0-9 ()/_-]{0,79}\Z")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_KINDS = frozenset({"https_url", "text", "secret"})
_MAX_SCHEMA_BYTES = 16 * 1024


@dataclass(frozen=True, slots=True)
class CatalogField:
    name: str
    label: str
    kind: str
    required: bool
    write_only: bool

    def __post_init__(self) -> None:
        if (
            not _NAME.fullmatch(self.name)
            or not _LABEL.fullmatch(self.label)
            or self.kind not in _KINDS
            or type(self.required) is not bool
            or type(self.write_only) is not bool
            or (self.kind == "secret") != self.write_only
        ):
            raise ValueError("invalid connector field")


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    name: str
    distribution: str
    version: str
    schema_digest: str
    fields: tuple[CatalogField, ...]
    execution_allowed: bool = field(default=False, init=False)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate connector schema key")
        result[key] = value
    return result


def reviewed_catalog_entry(
    reviewed: ReviewedConnector, *, schema_bytes: bytes, schema_sha256: str
) -> CatalogEntry:
    """Match installed metadata and a small reviewed schema without importing code."""
    if (
        type(schema_bytes) is not bytes
        or len(schema_bytes) > _MAX_SCHEMA_BYTES
        or type(schema_sha256) is not str
        or not _HASH.fullmatch(schema_sha256)
        or hashlib.sha256(schema_bytes).hexdigest() != schema_sha256
    ):
        raise ValueError("unreviewed connector schema")
    registration = discover(reviewed)
    try:
        document = json.loads(
            schema_bytes.decode("utf-8"), object_pairs_hook=_unique_object
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid connector schema") from exc
    if (
        type(document) is not dict
        or set(document) != {"schema_version", "connector", "fields"}
        or document["schema_version"] != "orion.connector-configuration.v1"
        or document["connector"] != registration.name
        or type(document["fields"]) is not list
        or not 1 <= len(document["fields"]) <= 32
    ):
        raise ValueError("invalid connector schema")
    fields = []
    for raw in document["fields"]:
        if type(raw) is not dict or set(raw) != {
            "name", "label", "kind", "required", "write_only"
        }:
            raise ValueError("invalid connector field")
        fields.append(CatalogField(**raw))
    if len({field.name for field in fields}) != len(fields):
        raise ValueError("duplicate connector field")
    return CatalogEntry(
        registration.name,
        registration.distribution,
        registration.version,
        schema_sha256,
        tuple(fields),
    )
