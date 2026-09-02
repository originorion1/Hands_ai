"""Governed ERPNext composition for non-submittable company records."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..contracts import Observation
from ..learning.autonomous_loop import (
    AuthorizationEnvelope,
    AuthorizedStudyRequest,
    StudyOutcome,
)
from ..learning.governed_record_evidence import run_governed_record_evidence
from ..understanding.metadata import (
    MetadataUnderstanding,
    is_collection_relationship,
)
from .erpnext_company_record_sample import ERPNextCompanyRecordSampleAdapter


def run_erpnext_company_record_evidence(
    request: AuthorizedStudyRequest,
    *,
    envelope: AuthorizationEnvelope,
    understanding: MetadataUnderstanding,
    base_url: str,
    company: str,
    api_key: str,
    api_secret: str,
    opener: Callable[..., Any] | None = None,
    evidence_sink: (
        Callable[[AuthorizedStudyRequest, tuple[Observation, ...]], None]
        | None
    ) = None,
) -> StudyOutcome:
    """Run one governed company-record study through the bounded adapter."""

    def reader(
        entity: str,
        fields: tuple[str, ...],
        requested_records: int,
    ) -> tuple[Observation, ...]:
        matches = [item for item in understanding.entities if item.doctype == entity]
        if len(matches) != 1:
            raise ValueError("authorized entity must exist exactly once")
        structural_entity = matches[0]
        if structural_entity.is_submittable:
            raise ValueError("company record entity must be non-submittable")
        if structural_entity.is_child_table:
            raise ValueError("company record entity must not be a child table")
        if structural_entity.is_single:
            raise ValueError("company record entity must not be single")
        structural_fields = {
            field.fieldname: field for field in structural_entity.fields
        }
        if "company" not in structural_fields:
            raise ValueError("company record entity requires a company field")
        if any(field not in structural_fields for field in fields):
            raise ValueError("authorized field is absent from structural understanding")
        if any(is_collection_relationship(structural_fields[field]) for field in fields):
            raise ValueError("company record evidence does not support collection fields")
        sample_fields = tuple(dict.fromkeys((*fields, "name", "company")))
        return ERPNextCompanyRecordSampleAdapter(
            base_url=base_url,
            tenant_id=request.tenant_id,
            api_key=api_key,
            api_secret=api_secret,
            resource=entity,
            company=company,
            fields=sample_fields,
            sample_size=requested_records,
            opener=opener,
        ).discover()

    return run_governed_record_evidence(
        request,
        envelope=envelope,
        understanding=understanding,
        reader=reader,
        evidence_sink=evidence_sink,
    )
