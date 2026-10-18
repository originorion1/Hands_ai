"""Custody-checked structural study from one completed metadata acquisition."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from pathlib import Path

from orion.stores.private_files import PrivateFileError, read_private_file

from .bounded_metadata import MetadataGrant, MetadataSessionDenied, _json, _private_write


def _read(path: Path) -> tuple[bytes, dict]:
    try:
        body = read_private_file(path, label="metadata study evidence", maximum_bytes=256 * 1024)
        value = json.loads(body)
    except (PrivateFileError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MetadataSessionDenied("metadata study custody denied") from exc
    if type(value) is not dict:
        raise MetadataSessionDenied("metadata study evidence invalid")
    return body, value


def _view(grant: MetadataGrant, directory: Path) -> dict[str, object]:
    _, scope = _read(directory / "scope.json")
    report_body, report = _read(directory / "report.json")
    if (scope.get("authorization_id") != grant.authorization_id
            or scope.get("tenant_id") != grant.tenant_id
            or scope.get("company") != grant.company
            or scope.get("origin") != grant.origin
            or scope.get("scopes") != [[name, list(fields)] for name, fields in grant.fields_by_doctype]
            or report.get("status") != "complete"):
        raise MetadataSessionDenied("metadata study grant or completion mismatch")
    studied = report.get("studied_doctypes")
    digests = report.get("evidence_digests")
    if (type(studied) is not list or type(digests) is not list
            or not studied or len(studied) != len(digests)
            or report.get("attempted_requests") != len(studied)):
        raise MetadataSessionDenied("metadata study evidence index invalid")
    links: list[dict[str, str]] = []
    total = 0
    for number, (name, digest) in enumerate(zip(studied, digests, strict=True), 1):
        body, evidence = _read(directory / f"evidence-{number:02d}.json")
        if (name not in dict(grant.fields_by_doctype)
                or evidence.get("doctype") != name
                or type(digest) is not str
                or not hmac.compare_digest(hashlib.sha256(body).hexdigest(), digest)):
            raise MetadataSessionDenied("metadata study evidence digest mismatch")
        total += len(body)
        rows = evidence.get("fields")
        if type(rows) is not list:
            raise MetadataSessionDenied("metadata study fields invalid")
        for row in rows:
            if type(row) is not dict or row.get("fieldname") not in dict(grant.fields_by_doctype)[name]:
                raise MetadataSessionDenied("metadata study field outside grant")
            if (row.get("fieldtype") in {"Link", "Table", "Table MultiSelect"}
                    and row.get("options") in dict(grant.fields_by_doctype)):
                links.append({"source": name, "field": row["fieldname"],
                              "target": row["options"], "evidence_sha256": digest})
    if total != report.get("evidence_bytes") or total > grant.max_evidence_bytes:
        raise MetadataSessionDenied("metadata study evidence volume mismatch")
    selected = links[0] if links else None
    return {
        "report_kind": "metadata_study",
        "lineage": {"tenant_id": grant.tenant_id, "authorization_id": grant.authorization_id,
                    "run_id": hashlib.sha256(report_body).hexdigest(),
                    "evidence_ids": digests, "source_sha256": hashlib.sha256(report_body).hexdigest()},
        "authority": {"execution_allowed": False, "erp_writes": 0},
        "study": {"observed_doctypes": studied, "observed_links": links,
                  "selected_question": (
                      f"Does {selected['source']}.{selected['field']} relate to "
                      f"{selected['target']} in permitted records?" if selected else None),
                  "conclusion": "Structural relationship observed; operational effect unverified"
                  if selected else "No permitted structural relationship observed",
                  "status": "proposal_only" if selected else "abstained",
                  "reviewer_decision": "pending"},
    }


def persist_metadata_study(*, grant: MetadataGrant, state_directory: Path) -> tuple[Path, str]:
    """Persist one immutable derived report; no network or grant expansion."""
    view = _view(grant, state_directory)
    body = _json(view)
    path = state_directory / "study.json"
    _private_write(path, body)
    return path, hashlib.sha256(body).hexdigest()


@dataclass(frozen=True, slots=True)
class MetadataStudyReportSource:
    grant: MetadataGrant
    state_directory: Path
    report_sha256: str

    @property
    def tenant_id(self) -> str:
        return self.grant.tenant_id

    def read_view(self) -> dict[str, object]:
        body, persisted = _read(self.state_directory / "study.json")
        if (not hmac.compare_digest(hashlib.sha256(body).hexdigest(), self.report_sha256)
                or persisted != _view(self.grant, self.state_directory)):
            raise MetadataSessionDenied("metadata study report invalidated")
        return persisted
