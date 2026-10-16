"""ERPNext boundary for structurally routed governed record studies."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..contracts import Observation
from ..learning.autonomous_loop import (
    AuthorizationEnvelope,
    AuthorizedStudyRequest,
    StudyOutcome,
    UnsupportedStudyCapabilityError,
    authorize_intent,
)
from ..learning.study_capability import StudyCapability, derive_study_capability
from ..understanding.metadata import MetadataUnderstanding
from .erpnext_company_record_evidence import (
    run_erpnext_company_record_evidence,
)
from .erpnext_governed_record_evidence import (
    run_erpnext_submitted_company_record_evidence,
)
from .erpnext_identity_record_evidence import (
    run_erpnext_identity_record_evidence,
)


def run_erpnext_governed_study(
    request: AuthorizedStudyRequest,
    *,
    envelope: AuthorizationEnvelope,
    understanding: MetadataUnderstanding,
    base_url: str,
    api_key: str,
    api_secret: str,
    company: str | None = None,
    record_identity: str | None = None,
    opener: Callable[..., Any] | None = None,
    evidence_sink: (
        Callable[[AuthorizedStudyRequest, tuple[Observation, ...]], None] | None
    ) = None,
) -> StudyOutcome:
    """Reauthorize and route one record study at the vendor boundary."""

    if not isinstance(request, AuthorizedStudyRequest):
        raise TypeError("request must be AuthorizedStudyRequest")
    reauthorized = authorize_intent(request.intent, envelope, understanding)
    if reauthorized != request:
        raise ValueError("request does not match current authorization")
    capability = derive_study_capability(reauthorized.intent, understanding)

    entity = next(
        (
            item
            for item in understanding.entities
            if item.doctype == reauthorized.intent.entity
        ),
        None,
    )
    if entity is None:
        raise UnsupportedStudyCapabilityError(
            f"no compatible ERPNext reader for {capability.value}"
        )
    has_company_scope = "company" in {field.fieldname for field in entity.fields}

    common = {
        "envelope": envelope,
        "understanding": understanding,
        "base_url": base_url,
        "api_key": api_key,
        "api_secret": api_secret,
        "opener": opener,
        "evidence_sink": evidence_sink,
    }
    if capability is StudyCapability.ORDINARY_RECORD:
        if has_company_scope:
            if record_identity is not None:
                raise UnsupportedStudyCapabilityError(
                    "company-scoped record reader does not accept identity scope"
                )
            if company is None:
                raise UnsupportedStudyCapabilityError(
                    "company-scoped record reader requires exact company scope"
                )
            return run_erpnext_company_record_evidence(
                reauthorized,
                company=company,
                **common,
            )
        if company is not None:
            raise UnsupportedStudyCapabilityError(
                "company scope requires a structural company field"
            )
        if record_identity is None:
            raise UnsupportedStudyCapabilityError(
                "non-company record reader requires exact identity scope"
            )
        return run_erpnext_identity_record_evidence(
            reauthorized,
            record_identity=record_identity,
            **common,
        )
    if capability is StudyCapability.SUBMITTED_DOCUMENT:
        if record_identity is not None:
            raise UnsupportedStudyCapabilityError(
                "submitted-document reader does not accept identity scope"
            )
        if not has_company_scope:
            raise UnsupportedStudyCapabilityError(
                "submitted-document reader requires structural company scope"
            )
        if company is None:
            raise UnsupportedStudyCapabilityError(
                "submitted-document reader requires exact company scope"
            )
        return run_erpnext_submitted_company_record_evidence(
            reauthorized,
            company=company,
            **common,
        )
    raise UnsupportedStudyCapabilityError(
        f"no compatible ERPNext reader for {capability.value}"
    )


route_erpnext_governed_record_evidence = run_erpnext_governed_study

__all__ = [
    "route_erpnext_governed_record_evidence",
    "run_erpnext_governed_study",
]
