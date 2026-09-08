"""Vendor-neutral descriptive investigation results, never learned facts."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class InvestigationDisposition:
    tenant_id: str
    company: str
    entity: str
    field: str
    relevant_evidence_sha256: str
    source_provenance_sha256: str
    missing_identities: int
    valid_identities: int
    absent_identities: int
    repeated_observations: int
    claim: str = "missing_values_observed"
    uncertainty: str = "applicability_unknown"
    exceptions: str = "valid_values_are_counterexamples_to_universal_missingness"
    status: str = "inconclusive"
    schema: int = 1
    execution_allowed: bool = False
    promotion_allowed: bool = False
    recommendation_allowed: bool = False
    business_defect_validated: bool = False

    def __post_init__(self):
        for value in (self.tenant_id, self.company, self.entity, self.field):
            if type(value) is not str or not value.strip() or value != value.strip() or "*" in value:
                raise ValueError("disposition requires exact scope")
        if any(type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value) for value in (
            self.relevant_evidence_sha256, self.source_provenance_sha256,
        )):
            raise ValueError("disposition evidence digest is invalid")
        if any(type(value) is not int or value < 0 for value in (
            self.missing_identities, self.valid_identities, self.absent_identities,
            self.repeated_observations,
        )) or self.missing_identities == 0:
            raise ValueError("disposition counts are invalid")
        if type(self.schema) is not int or self.schema != 1 or self.status != "inconclusive":
            raise ValueError("unsupported disposition")
        if (
            self.claim != "missing_values_observed" or self.uncertainty != "applicability_unknown"
            or self.exceptions != "valid_values_are_counterexamples_to_universal_missingness"
        ):
            raise ValueError("unsupported descriptive claim")
        if any(value is not False for value in (
            self.execution_allowed, self.promotion_allowed, self.recommendation_allowed,
            self.business_defect_validated,
        )):
            raise ValueError("investigation dispositions cannot grant authority")


def disposition_from_finding(finding) -> InvestigationDisposition:
    return InvestigationDisposition(
        finding.tenant_id, finding.company, finding.entity, finding.field,
        finding.relevant_evidence_sha256, finding.source_provenance_sha256,
        sum(item.missing for item in finding.evidence),
        sum(not item.missing for item in finding.evidence), finding.absent_identity_count,
        finding.repeated_observation_count,
    )


def disposition_to_json(disposition: InvestigationDisposition) -> str:
    if not isinstance(disposition, InvestigationDisposition):
        raise TypeError("explicit disposition required")
    disposition.__post_init__()
    return json.dumps(asdict(disposition), sort_keys=True, separators=(",", ":"))


def disposition_from_json(body: str) -> InvestigationDisposition:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate disposition key")
            result[key] = value
        return result
    payload = json.loads(body, object_pairs_hook=unique)
    if type(payload) is not dict or set(payload) != set(InvestigationDisposition.__dataclass_fields__):
        raise ValueError("disposition fields differ")
    return InvestigationDisposition(**payload)
