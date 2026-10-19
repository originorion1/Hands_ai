"""Review-only customer onboarding and read-scope proposals.

These forms never create connector grants or open a network connection.
Operator-approved bootstrap and governed authorization remain separate steps.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from orion.discovery.erpnext_adapter import _validate_resource
from orion.discovery.erpnext_live_session import is_sensitive_metadata_name

from .tenants import is_canonical_tenant_slug

_COUNTRY = re.compile(r"[A-Z]{2}\Z")
_NAME = re.compile(r"[^\x00-\x1f\x7f]{1,128}\Z")
_REF = re.compile(r"[^\x00-\x1f\x7f]{1,128}\Z")


class PortalProposalDenied(ValueError):
    """A customer proposal cannot be submitted for review."""


@dataclass(frozen=True, slots=True)
class OrganizationProposal:
    organization_name: str
    tenant_id: str
    external_company_reference: str = field(repr=False)
    country: str = ""
    timezone: str = ""
    administrator_issuer: str = ""
    administrator_subject: str = ""
    retention_days: int = 0

    def __post_init__(self) -> None:
        if (
            type(self.organization_name) is not str
            or not _NAME.fullmatch(self.organization_name)
            or not is_canonical_tenant_slug(self.tenant_id)
            or type(self.external_company_reference) is not str
            or not _REF.fullmatch(self.external_company_reference)
            or type(self.country) is not str or not _COUNTRY.fullmatch(self.country)
            or type(self.administrator_issuer) is not str
            or not self.administrator_issuer.startswith("https://")
            or type(self.administrator_subject) is not str
            or not self.administrator_subject.strip()
            or type(self.retention_days) is not int
            or not 1 <= self.retention_days <= 3650
        ):
            raise PortalProposalDenied("invalid organization proposal")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, TypeError, ValueError) as exc:
            raise PortalProposalDenied("invalid organization timezone") from exc

    def review_packet(self) -> dict[str, object]:
        """Return a proposal, never an approved bootstrap or tenant membership."""
        return {"kind": "organization_proposal", "status": "pending_operator_review",
                "execution_authority": False, **asdict(self)}


@dataclass(frozen=True, slots=True)
class AuthorizationProposal:
    tenant_id: str
    companies: tuple[str, ...]
    fields_by_doctype: tuple[tuple[str, tuple[str, ...]], ...]
    sensitive_exclusions: tuple[str, ...]
    historical_start: date
    historical_end: date
    max_requests: int
    max_duration_seconds: int
    max_evidence_bytes: int
    retention_days: int
    revocation_operator: str
    read_only: bool = True

    def __post_init__(self) -> None:
        if (
            not is_canonical_tenant_slug(self.tenant_id)
            or type(self.companies) is not tuple or not self.companies
            or any(type(value) is not str or not _REF.fullmatch(value)
                   for value in self.companies)
            or len(set(self.companies)) != len(self.companies)
            or type(self.fields_by_doctype) is not tuple or not self.fields_by_doctype
            or any(type(row) is not tuple or len(row) != 2 or type(row[0]) is not str
                   for row in self.fields_by_doctype)
            or type(self.sensitive_exclusions) is not tuple or not self.sensitive_exclusions
            or any(type(value) is not str or not value.strip()
                   for value in self.sensitive_exclusions)
            or not isinstance(self.historical_start, date)
            or not isinstance(self.historical_end, date)
            or self.historical_start > self.historical_end
            or self.historical_end > datetime.now(UTC).date()
            or type(self.max_requests) is not int or not 1 <= self.max_requests <= 25
            or type(self.max_duration_seconds) is not int
            or not 1 <= self.max_duration_seconds <= 1800
            or type(self.max_evidence_bytes) is not int
            or not 1 <= self.max_evidence_bytes <= 256 * 1024
            or type(self.retention_days) is not int or not 1 <= self.retention_days <= 3650
            or type(self.revocation_operator) is not str
            or not _NAME.fullmatch(self.revocation_operator)
            or self.read_only is not True
        ):
            raise PortalProposalDenied("invalid read-only authorization proposal")
        if len({name for name, _ in self.fields_by_doctype}) != len(self.fields_by_doctype):
            raise PortalProposalDenied("duplicate DocType scope")
        for doctype, fields in self.fields_by_doctype:
            try:
                _validate_resource(doctype)
                if (is_sensitive_metadata_name(doctype) or type(fields) is not tuple
                        or not fields or any(type(name) is not str for name in fields)
                        or len(set(fields)) != len(fields)):
                    raise PortalProposalDenied("sensitive or ambiguous DocType scope")
                for name in fields:
                    _validate_resource(name)
                    if is_sensitive_metadata_name(name):
                        raise PortalProposalDenied("sensitive field scope")
            except ValueError as exc:
                raise PortalProposalDenied("invalid DocType or field scope") from exc

    def review_packet(self) -> dict[str, object]:
        """Record customer intent; a Kernel grant must be reviewed separately."""
        scope = {
            "tenant_id": self.tenant_id, "companies": list(self.companies),
            "fields_by_doctype": [[name, list(fields)] for name, fields in self.fields_by_doctype],
            "sensitive_exclusions": list(self.sensitive_exclusions),
            "historical_start": self.historical_start.isoformat(),
            "historical_end": self.historical_end.isoformat(),
            "max_requests": self.max_requests,
            "max_duration_seconds": self.max_duration_seconds,
            "max_evidence_bytes": self.max_evidence_bytes,
            "retention_days": self.retention_days,
            "revocation_operator": self.revocation_operator,
            "read_only": True,
        }
        digest = authorization_scope_digest(scope)
        return {"kind": "authorization_proposal", "status": "pending_governed_validation",
                "execution_authority": False, "scope_sha256": digest, **scope}


def authorization_scope_digest(scope: dict[str, object]) -> str:
    """Canonical digest of the customer-proposed scope, never a grant signature."""
    return hashlib.sha256(
        json.dumps(scope, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
