"""One reviewed, metadata-only ERPNext study; no implicit network authority.

The caller supplies a trusted grant lookup and a confined HTTPS transport.
This module never discovers credentials, grants access, or opens a socket.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import time
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from uuid import NAMESPACE_URL, uuid5

from orion.contracts import Evidence, EvidenceKind, Observation
from orion.discovery.credential_references import CredentialEnvironmentReferences
from orion.discovery.erpnext_adapter import _normalize_base_url, _validate_resource
from orion.discovery.erpnext_live_session import is_sensitive_metadata_name
from orion.discovery.erpnext_metadata_adapter import ERPNextMetadataAdapter
from orion.discovery.planner import DiscoveryAuthorization, plan_authorized_discovery
from orion.understanding.metadata import build_metadata_understanding, relationship_target

from .tenants import is_canonical_tenant_slug

MAX_ATTEMPTS = 25
MAX_SECONDS = 30 * 60
MAX_RESPONSE_BYTES = 64 * 1024
MAX_EVIDENCE_BYTES = 256 * 1024
MAX_PLANNER_CANDIDATES = 100
_SCHEMA_KEYS = frozenset({"fieldname", "fieldtype", "options", "reqd", "permlevel"})


class MetadataSessionDenied(ValueError):
    """The fixed read scope or continuing authorization cannot be upheld."""


def _json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       allow_nan=False) + "\n").encode()


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _private_write(path: Path, body: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags, 0o600), "wb") as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())


@dataclass(frozen=True, slots=True)
class MetadataGrant:
    authorization_id: str
    tenant_id: str
    external_customer_reference: str = field(repr=False)
    company: str = field(repr=False)
    origin: str = field(repr=False)
    credential_references: CredentialEnvironmentReferences = field(repr=False)
    credential_vault_reference: str = field(default="", repr=False)
    fields_by_doctype: tuple[tuple[str, tuple[str, ...]], ...] = ()
    seed_doctype: str = ""
    starts_at: datetime = field(default_factory=datetime.now)
    expires_at: datetime = field(default_factory=datetime.now)
    max_attempts: int = MAX_ATTEMPTS
    max_response_bytes: int = MAX_RESPONSE_BYTES
    max_evidence_bytes: int = MAX_EVIDENCE_BYTES
    retention_days: int = 1

    def __post_init__(self) -> None:
        if (
            not self.authorization_id or not is_canonical_tenant_slug(self.tenant_id)
            or not self.company
        ):
            raise MetadataSessionDenied("exact identity and authorization required")
        if not self.external_customer_reference:
            raise MetadataSessionDenied("separate external customer reference required")
        if _normalize_base_url(self.origin) != self.origin:
            raise MetadataSessionDenied("exact HTTPS origin required")
        if not isinstance(self.credential_references, CredentialEnvironmentReferences):
            raise MetadataSessionDenied("credential references required")
        if (type(self.credential_vault_reference) is not str
                or (self.credential_vault_reference and
                    (not self.credential_vault_reference.startswith("vault:")
                     or len(self.credential_vault_reference) > 256))):
            raise MetadataSessionDenied("reviewed vault reference required")
        if (
            self.starts_at.utcoffset() is None or self.expires_at.utcoffset() is None
            or self.expires_at <= self.starts_at
            or self.expires_at - self.starts_at > timedelta(seconds=MAX_SECONDS)
        ):
            raise MetadataSessionDenied("bounded timezone-aware grant required")
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= MAX_ATTEMPTS:
            raise MetadataSessionDenied("request ceiling exceeds metadata allowance")
        if (
            type(self.max_response_bytes) is not int
            or not 1 <= self.max_response_bytes <= MAX_RESPONSE_BYTES
            or type(self.max_evidence_bytes) is not int
            or not 1 <= self.max_evidence_bytes <= MAX_EVIDENCE_BYTES
        ):
            raise MetadataSessionDenied("evidence volume exceeds allowance")
        if type(self.retention_days) is not int or not 1 <= self.retention_days <= 30:
            raise MetadataSessionDenied("metadata retention must be one to thirty days")
        if not self.fields_by_doctype or len(self.fields_by_doctype) != len({
            name for name, _ in self.fields_by_doctype
        }):
            raise MetadataSessionDenied("explicit unique DocType scopes required")
        if self.seed_doctype not in dict(self.fields_by_doctype):
            raise MetadataSessionDenied("seed DocType must be allowed")
        for doctype, fields in self.fields_by_doctype:
            _validate_resource(doctype)
            if is_sensitive_metadata_name(doctype):
                raise MetadataSessionDenied("sensitive DocType excluded")
            if not fields or len(fields) != len(set(fields)):
                raise MetadataSessionDenied("exact unique field allowlist required")
            for name in fields:
                _validate_resource(name)
                if is_sensitive_metadata_name(name):
                    raise MetadataSessionDenied("sensitive field excluded")


@dataclass(frozen=True, slots=True)
class MetadataSessionResult:
    status: str
    attempted_requests: int
    evidence_bytes: int
    evidence_digests: tuple[str, ...]
    studied_doctypes: tuple[str, ...]
    next_question: str | None
    stopping_reason: str = "no_authorized_candidate"


def _project(raw: Mapping[str, object], doctype: str,
             allowed: tuple[str, ...]) -> dict[str, object]:
    try:
        docs = raw["message"]["docs"]
        if not isinstance(docs, list) or any(not isinstance(item, dict) for item in docs):
            raise MetadataSessionDenied("invalid metadata documents")
        matching = [item for item in docs if item.get("name") == doctype]
        if len(matching) != 1:
            raise MetadataSessionDenied("metadata document identity is invalid")
        doc = matching[0]
        rows = doc["fields"]
    except (ValueError, KeyError, TypeError) as exc:
        raise MetadataSessionDenied("invalid metadata response") from exc
    if not isinstance(rows, list):
        raise MetadataSessionDenied("invalid metadata fields")
    selected: dict[str, dict[str, object]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise MetadataSessionDenied("invalid metadata field")
        name = row.get("fieldname")
        if name not in allowed:
            continue
        if name in selected or not isinstance(row.get("fieldtype"), str):
            raise MetadataSessionDenied("duplicate or invalid permitted field")
        projected = {key: row[key] for key in _SCHEMA_KEYS if key in row}
        if any(type(value) not in (str, int, bool) for value in projected.values()):
            raise MetadataSessionDenied("invalid permitted field metadata")
        selected[name] = projected
    if set(selected) != set(allowed):
        raise MetadataSessionDenied("permitted metadata field is missing")
    return {"doctype": doctype, "fields": [selected[name] for name in allowed]}


def _select_next_structure(
    grant: MetadataGrant,
    observations: tuple[Observation, ...],
    evidence_digests: Mapping[str, str],
    *,
    sequence: int,
) -> dict[str, object]:
    """Select only a planner-proposed target supported by scoped metadata."""
    allowed = frozenset(dict(grant.fields_by_doctype))
    understanding = build_metadata_understanding(
        observations,
        tenant_id=grant.tenant_id,
        allowed_doctypes=allowed,
    )
    plan = plan_authorized_discovery(
        understanding,
        authorization=DiscoveryAuthorization(
            tenant_id=grant.tenant_id,
            metadata_targets=allowed,
            record_targets=frozenset(),
            max_targets=MAX_PLANNER_CANDIDATES,
        ),
    )
    evidence_by_id = {
        str(observation.evidence.evidence_id): evidence_digests[
            observation.evidence.payload["doctype"]
        ]
        for observation in observations
    }
    candidates: list[dict[str, object]] = []
    for item in plan.items:
        # The generic planner filters observed edges by this exact allowlist;
        # retain the explicit check at the acquisition boundary as defense in depth.
        if item.target not in allowed:
            continue
        receipts = sorted({
            evidence_by_id[str(provenance)]
            for provenance in item.provenance_ids
            if str(provenance) in evidence_by_id
        })
        if not receipts:
            raise MetadataSessionDenied("planner provenance is unavailable")
        sources: list[dict[str, str]] = []
        for entity in understanding.entities:
            for structural_field in entity.fields:
                if relationship_target(structural_field) != item.target:
                    continue
                source_digest = evidence_by_id.get(str(entity.provenance_ids[0]))
                if source_digest is not None:
                    sources.append({
                        "doctype": entity.doctype,
                        "field": structural_field.fieldname,
                        "evidence_sha256": source_digest,
                    })
        question = (
            f"What permitted structure does {item.target} expose, given "
            "the observed authorized relationship?"
        )
        candidates.append({
            "rank": len(candidates) + 1,
            "target": item.target,
            "kind": item.kind.value,
            "question": question,
            "rationale": item.rationale,
            "triggering_sources": sources[:16],
            "additional_source_count": max(0, len(sources) - 16),
            "evidence_sha256": receipts,
            "estimated_request_cost": 1,
            "estimated_information_value": None,
        })
    selected = candidates[0] if candidates else None
    return {
        "sequence": sequence,
        "selector": "orion.discovery.planner.plan_authorized_discovery",
        "policy": "rank authorized unseen structural references in planner order",
        "candidates": candidates,
        "selected_target": selected["target"] if selected else None,
        "selected_question": selected["question"] if selected else None,
        "selection_rationale": selected["rationale"] if selected else None,
        "alternatives": candidates[1:],
        "evidence_sha256": sorted({
            digest for candidate in candidates
            for digest in candidate["evidence_sha256"]
        }),
        "uncertainty": (
            "The planner is deterministic and has no calibrated information-value "
            "estimate; structural relevance does not establish operational impact."
        ),
        "stopping_reason": "no_authorized_candidate" if not candidates else None,
    }


def run_bounded_metadata_session(
    grant: MetadataGrant,
    *,
    lookup: Callable[[str], MetadataGrant | None],
    environment: Mapping[str, str],
    transport: Callable[..., object],
    commit_scope: Callable[[str], AbstractContextManager[None]],
    state_directory: Path,
    clock: Callable[[], datetime],
    monotonic: Callable[[], float] = time.monotonic,
) -> MetadataSessionResult:
    """Study authorized structural links and persist only projected evidence.

    A pre-existing state directory always denies restart, including after a
    failed or interrupted attempt. Every request is reserved before transport.
    ``commit_scope`` must serialize publication with grant revocation in the
    trusted control-plane ledger; it is not a grant supplied by this module.
    """
    if (not isinstance(grant, MetadataGrant) or not callable(lookup)
            or not callable(transport) or not callable(commit_scope)):
        raise MetadataSessionDenied("reviewed grant, lookup, transport and commit scope required")
    if not state_directory.is_absolute() or state_directory.is_symlink():
        raise MetadataSessionDenied("private absolute state directory required")
    parent = state_directory.parent
    info = parent.stat()
    if (parent.is_symlink() or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700):
        raise MetadataSessionDenied("private owner-controlled state parent required")
    if state_directory.exists():
        raise MetadataSessionDenied("session already has durable state")

    started_mono = monotonic()
    last_clock = grant.starts_at

    def check() -> datetime:
        nonlocal last_clock
        now = clock()
        if (
            not isinstance(now, datetime) or now.utcoffset() is None
            or now < grant.starts_at or now >= grant.expires_at
            or now < last_clock or monotonic() - started_mono >= MAX_SECONDS
            or lookup(grant.authorization_id) != grant
        ):
            raise MetadataSessionDenied("authorization revoked, expired or changed")
        last_clock = now
        return now

    check()
    credentials = grant.credential_references.resolve(environment)
    state_directory.mkdir(mode=0o700)
    os.chmod(state_directory, 0o700)
    _private_write(state_directory / "scope.json", _json({
        "authorization_id": grant.authorization_id, "tenant_id": grant.tenant_id,
        "external_customer_reference": grant.external_customer_reference,
        "company": grant.company,
        "origin": grant.origin, "starts_at": grant.starts_at.isoformat(),
        "expires_at": grant.expires_at.isoformat(), "max_attempts": grant.max_attempts,
        "max_evidence_bytes": grant.max_evidence_bytes,
        "retention_until": (grant.expires_at + timedelta(days=grant.retention_days)).isoformat(),
        "scopes": [[name, list(fields)] for name, fields in grant.fields_by_doctype],
    }))
    scopes = dict(grant.fields_by_doctype)
    observations: list[Observation] = []
    observed_digests: dict[str, str] = {}
    decisions: list[dict[str, object]] = []
    decision_digests: list[str] = []

    def persist_decision(decision: dict[str, object]) -> None:
        body = _json(decision)
        if len(body) > MAX_RESPONSE_BYTES:
            raise MetadataSessionDenied("study decision exceeds evidence bound")
        number = len(decisions) + 1
        with commit_scope(grant.authorization_id):
            check()
            _private_write(state_directory / f"decision-{number:02d}.json", body)
        decisions.append(decision)
        decision_digests.append(_digest(body))

    # The signed grant supplies the first seed. Later targets are selected by
    # ORION from relationships in acquired, field-projected evidence.
    persist_decision({
        "sequence": 1,
        "selector": "signed_grant_seed",
        "policy": "initial target fixed by separately approved grant",
        "candidates": [],
        "selected_target": grant.seed_doctype,
        "selected_question": None,
        "selection_rationale": "authorized initial metadata seed",
        "alternatives": [],
        "evidence_sha256": [],
        "uncertainty": "The grant chose the initial seed; this is not an ORION selection.",
        "stopping_reason": None,
    })
    studied: list[str] = []
    digests: list[str] = []
    evidence_bytes = 0
    attempts = 0
    doctype: str | None = grant.seed_doctype
    stopping_reason = "no_authorized_candidate"
    while doctype is not None:
        check()
        if attempts >= grant.max_attempts:
            persist_decision({
                "sequence": attempts + 1,
                "selector": "orion.discovery.planner.plan_authorized_discovery",
                "policy": "request budget exhausted before another acquisition",
                "candidates": [], "selected_target": doctype,
                "selected_question": None,
                "selection_rationale": "an authorized target remained, but no request budget remained",
                "alternatives": [], "evidence_sha256": list(digests),
                "uncertainty": "The target was not acquired; no conclusion is supported.",
                "stopping_reason": "request_budget_exhausted",
            })
            raise MetadataSessionDenied("metadata request ceiling exhausted")
        if doctype not in scopes or doctype in studied:
            raise MetadataSessionDenied("planner selected a target outside grant scope")
        attempts += 1
        path = "/api/method/frappe.desk.form.load.getdoctype"
        _private_write(state_directory / f"attempt-{attempts:02d}.json", _json({
            "attempt": attempts, "doctype": doctype, "at": last_clock.isoformat(),
            "method": "GET", "path": path,
        }))

        expected = f"{grant.origin}{path}?{urlencode({'doctype': doctype})}"

        @contextmanager
        def guarded_transport(request: object, *, timeout: int,
                              expected_url: str = expected):
            if (request.get_method() != "GET" or request.full_url != expected_url
                    or timeout != 20):
                raise MetadataSessionDenied("unreviewed metadata request denied")
            with transport(request, timeout=timeout) as response:
                final_url = getattr(response, "geturl", None)
                if not callable(final_url) or final_url() != expected_url:
                    raise MetadataSessionDenied("redirect or destination change denied")
                yield response

        acquired = ERPNextMetadataAdapter(
            base_url=grant.origin, tenant_id=grant.tenant_id,
            api_key=credentials.api_key, api_secret=credentials.api_secret,
            doctypes=(doctype,), max_response_bytes=grant.max_response_bytes,
            timeout_seconds=20, opener=guarded_transport,
        ).discover()
        check()
        projected = _project(acquired[0].evidence.payload["metadata"],
                             doctype, scopes[doctype])
        payload = _json(projected)
        if evidence_bytes + len(payload) > grant.max_evidence_bytes:
            raise MetadataSessionDenied("metadata evidence exceeds bound")
        with commit_scope(grant.authorization_id):
            check()
            _private_write(state_directory / f"evidence-{attempts:02d}.json", payload)
        evidence_bytes += len(payload)
        digest = _digest(payload)
        digests.append(digest)
        studied.append(doctype)
        evidence_id = uuid5(
            NAMESPACE_URL,
            f"orion-metadata:{grant.authorization_id}:{attempts}:{digest}",
        )
        observations.append(Observation(Evidence(
            kind=EvidenceKind.METADATA,
            source="erpnext_metadata",
            payload={"doctype": doctype, "metadata": {"docs": [{
                "name": doctype, "fields": projected["fields"],
            }]}},
            observed_at=last_clock,
            evidence_id=evidence_id,
            tenant_id=grant.tenant_id,
        )))
        observed_digests[doctype] = digest
        decision = _select_next_structure(
            grant,
            tuple(observations),
            observed_digests,
            sequence=attempts + 1,
        )
        selected = decision["selected_target"]
        if selected is not None and selected not in scopes:
            raise MetadataSessionDenied("planner target exceeds exact grant scope")
        if selected is not None and attempts >= grant.max_attempts:
            decision["stopping_reason"] = "request_budget_exhausted"
            persist_decision(decision)
            raise MetadataSessionDenied("metadata request ceiling exhausted")
        persist_decision(decision)
        if selected is None:
            doctype = None
        else:
            doctype = selected
    result = MetadataSessionResult("complete", attempts, evidence_bytes,
                                   tuple(digests), tuple(studied), None,
                                   stopping_reason)
    with commit_scope(grant.authorization_id):
        check()
        _private_write(state_directory / "report.json", _json({
            "status": result.status, "attempted_requests": attempts,
            "evidence_bytes": evidence_bytes, "evidence_digests": digests,
            "studied_doctypes": studied, "next_question": None,
            "decision_digests": decision_digests,
            "stopping_reason": stopping_reason,
        }))
    return result


def purge_expired_metadata_state(
    grant: MetadataGrant, *, state_directory: Path, now: datetime,
) -> int:
    """Delete only this grant's private evidence after its approved retention."""
    if (not isinstance(grant, MetadataGrant) or not isinstance(now, datetime)
            or now.utcoffset() is None
            or now < grant.expires_at + timedelta(days=grant.retention_days)):
        raise MetadataSessionDenied("metadata retention period remains active")
    if not state_directory.is_absolute() or state_directory.is_symlink():
        raise MetadataSessionDenied("private metadata state required")
    info = state_directory.stat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise MetadataSessionDenied("private metadata state custody denied")
    files = tuple(state_directory.iterdir())
    for path in files:
        info = path.lstat()
        name = path.name
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600
                or name not in {"scope.json", "report.json", "study.json"}
                and re.fullmatch(r"(?:attempt|evidence|decision)-[0-9]{2}\.json", name) is None
                or not name.endswith(".json")):
            raise MetadataSessionDenied("unreviewed metadata state file denied")
    scope_path = state_directory / "scope.json"
    if not scope_path.is_file():
        raise MetadataSessionDenied("metadata scope record missing")
    scope = json.loads(scope_path.read_bytes())
    if (scope.get("authorization_id") != grant.authorization_id
            or scope.get("tenant_id") != grant.tenant_id
            or scope.get("external_customer_reference") != grant.external_customer_reference
            or scope.get("company") != grant.company
            or scope.get("origin") != grant.origin
            or scope.get("starts_at") != grant.starts_at.isoformat()
            or scope.get("expires_at") != grant.expires_at.isoformat()
            or scope.get("max_attempts") != grant.max_attempts
            or scope.get("max_evidence_bytes") != grant.max_evidence_bytes
            or scope.get("scopes") != [[name, list(fields)] for name, fields in grant.fields_by_doctype]
            or scope.get("retention_until") != (
                grant.expires_at + timedelta(days=grant.retention_days)
            ).isoformat()):
        raise MetadataSessionDenied("metadata retention scope mismatch")
    for path in files:
        path.unlink()
    state_directory.rmdir()
    return len(files)
