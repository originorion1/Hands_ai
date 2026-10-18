"""One reviewed, metadata-only ERPNext study; no implicit network authority.

The caller supplies a trusted grant lookup and a confined HTTPS transport.
This module never discovers credentials, grants access, or opens a socket.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import time
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

from orion.discovery.credential_references import CredentialEnvironmentReferences
from orion.discovery.erpnext_adapter import _normalize_base_url, _validate_resource
from orion.discovery.erpnext_live_session import is_sensitive_metadata_name
from orion.discovery.erpnext_metadata_adapter import ERPNextMetadataAdapter

from .tenants import is_canonical_tenant_slug

MAX_ATTEMPTS = 25
MAX_SECONDS = 30 * 60
MAX_RESPONSE_BYTES = 64 * 1024
MAX_EVIDENCE_BYTES = 256 * 1024
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
    fields_by_doctype: tuple[tuple[str, tuple[str, ...]], ...] = ()
    seed_doctype: str = ""
    starts_at: datetime = field(default_factory=datetime.now)
    expires_at: datetime = field(default_factory=datetime.now)
    max_attempts: int = MAX_ATTEMPTS
    max_response_bytes: int = MAX_RESPONSE_BYTES
    max_evidence_bytes: int = MAX_EVIDENCE_BYTES

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
        "scopes": [[name, list(fields)] for name, fields in grant.fields_by_doctype],
    }))
    scopes = dict(grant.fields_by_doctype)
    pending = [grant.seed_doctype]
    studied: list[str] = []
    digests: list[str] = []
    evidence_bytes = 0
    attempts = 0
    while pending:
        doctype = pending.pop(0)
        if doctype in studied:
            continue
        check()
        if attempts >= grant.max_attempts:
            raise MetadataSessionDenied("metadata request ceiling exhausted")
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

        observations = ERPNextMetadataAdapter(
            base_url=grant.origin, tenant_id=grant.tenant_id,
            api_key=credentials.api_key, api_secret=credentials.api_secret,
            doctypes=(doctype,), max_response_bytes=grant.max_response_bytes,
            timeout_seconds=20, opener=guarded_transport,
        ).discover()
        check()
        projected = _project(observations[0].evidence.payload["metadata"],
                             doctype, scopes[doctype])
        payload = _json(projected)
        if evidence_bytes + len(payload) > grant.max_evidence_bytes:
            raise MetadataSessionDenied("metadata evidence exceeds bound")
        with commit_scope(grant.authorization_id):
            check()
            _private_write(state_directory / f"evidence-{attempts:02d}.json", payload)
        evidence_bytes += len(payload)
        digests.append(_digest(payload))
        studied.append(doctype)
        for row in projected["fields"]:
            target = row.get("options")
            if (
                row.get("fieldtype") in {"Link", "Table", "Table MultiSelect"}
                and isinstance(target, str) and target in scopes
                and target not in studied and target not in pending
            ):
                pending.append(target)
        pending.sort()
    result = MetadataSessionResult("complete", attempts, evidence_bytes,
                                   tuple(digests), tuple(studied), None)
    with commit_scope(grant.authorization_id):
        check()
        _private_write(state_directory / "report.json", _json({
            "status": result.status, "attempted_requests": attempts,
            "evidence_bytes": evidence_bytes, "evidence_digests": digests,
            "studied_doctypes": studied, "next_question": None,
        }))
    return result
