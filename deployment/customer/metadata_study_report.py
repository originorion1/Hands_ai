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
    decision_digests = report.get("decision_digests")
    if (type(studied) is not list or type(digests) is not list
            or not studied or len(studied) != len(digests)
            or report.get("attempted_requests") != len(studied)
            or type(decision_digests) is not list
            or len(decision_digests) != len(studied) + 1
            or report.get("stopping_reason") != "no_authorized_candidate"):
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
    decisions: list[dict] = []
    allowed = dict(grant.fields_by_doctype)
    for number, digest in enumerate(decision_digests, 1):
        body, decision = _read(directory / f"decision-{number:02d}.json")
        if (type(digest) is not str
                or not hmac.compare_digest(hashlib.sha256(body).hexdigest(), digest)):
            raise MetadataSessionDenied("metadata study decision digest mismatch")
        candidates = decision.get("candidates")
        alternatives = decision.get("alternatives")
        selected_target = decision.get("selected_target")
        if (type(candidates) is not list or type(alternatives) is not list
                or alternatives != candidates[1:]
                or any(type(item) is not dict or item.get("target") not in allowed
                       or item.get("kind") != "metadata"
                       for item in candidates)):
            raise MetadataSessionDenied("metadata study candidate scope invalid")
        if number == 1:
            if (decision.get("selector") != "signed_grant_seed"
                    or selected_target != grant.seed_doctype or candidates):
                raise MetadataSessionDenied("metadata study seed decision invalid")
        elif number <= len(studied):
            expected_target = studied[number - 1]
            if (selected_target != expected_target or not candidates
                    or candidates[0].get("target") != selected_target
                    or decision.get("stopping_reason") is not None):
                raise MetadataSessionDenied("metadata study planner decision invalid")
        elif (selected_target is not None or candidates
              or decision.get("stopping_reason") != "no_authorized_candidate"):
            raise MetadataSessionDenied("metadata study stopping decision invalid")
        evidence_refs = decision.get("evidence_sha256")
        visible_digests = set(digests[:max(0, min(number - 1, len(digests)))])
        if (type(evidence_refs) is not list
                or any(reference not in visible_digests for reference in evidence_refs)):
            raise MetadataSessionDenied("metadata study decision evidence reference invalid")
        for rank, candidate in enumerate(candidates, 1):
            candidate_refs = candidate.get("evidence_sha256")
            sources = candidate.get("triggering_sources")
            if (candidate.get("rank") != rank
                    or type(candidate_refs) is not list or not candidate_refs
                    or any(reference not in visible_digests for reference in candidate_refs)
                    or type(sources) is not list
                    or any(type(source) is not dict
                           or source.get("doctype") not in allowed
                           or source.get("field") not in allowed[source["doctype"]]
                           or source.get("evidence_sha256") not in visible_digests
                           for source in sources)):
                raise MetadataSessionDenied("metadata study candidate provenance invalid")
        if candidates and (
            selected_target != candidates[0].get("target")
            or decision.get("selected_question") != candidates[0].get("question")
            or decision.get("selection_rationale") != candidates[0].get("rationale")
        ):
            raise MetadataSessionDenied("metadata study selection binding invalid")
        decisions.append(decision)
    planner_choices = [
        decision for decision in decisions[1:]
        if decision.get("selected_target") in studied
    ]
    selected_question = (
        planner_choices[-1].get("selected_question") if planner_choices else None
    )
    return {
        "report_kind": "metadata_study",
        "lineage": {"tenant_id": grant.tenant_id, "authorization_id": grant.authorization_id,
                    "run_id": hashlib.sha256(report_body).hexdigest(),
                    "evidence_ids": digests, "decision_sha256": decision_digests,
                    "source_sha256": hashlib.sha256(report_body).hexdigest()},
        "authority": {"execution_allowed": False, "erp_writes": 0},
        "study": {"observed_doctypes": studied, "observed_links": links,
                  "decisions": decisions,
                  "selected_question": selected_question,
                  "stopping_reason": report["stopping_reason"],
                  "conclusion": "Planner-selected structural target acquired; operational effect unverified"
                  if selected_question else "No permitted structural candidate observed",
                  "status": "proposal_only" if selected_question else "abstained",
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
