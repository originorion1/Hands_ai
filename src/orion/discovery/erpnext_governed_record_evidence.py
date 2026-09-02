"""Submitted, company-scoped ERPNext composition for governed evidence."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..learning.autonomous_loop import (
    AuthorizationEnvelope,
    AuthorizedStudyRequest,
    StudyOutcome,
)
from ..learning.governed_record_evidence import run_governed_record_evidence
from ..understanding.metadata import MetadataUnderstanding
from .erpnext_historical_sample import (
    MAX_SAMPLE_SIZE,
    ERPNextHistoricalSampleAdapter,
)


def run_erpnext_submitted_company_record_evidence(
    request: AuthorizedStudyRequest,
    *,
    envelope: AuthorizationEnvelope,
    understanding: MetadataUnderstanding,
    base_url: str,
    company: str,
    api_key: str,
    api_secret: str,
    opener: Callable[..., Any] | None = None,
) -> StudyOutcome:
    """Run one authorized study through the existing bounded GET adapter."""

    def reader():
        intent = request.intent
        if intent.requested_records > MAX_SAMPLE_SIZE:
            raise ValueError("requested records exceed ERPNext reader capacity")
        selected_field = intent.fields[0]
        fields = tuple(
            dict.fromkeys((selected_field, "name", "company", "docstatus"))
        )
        return ERPNextHistoricalSampleAdapter(
            base_url=base_url,
            tenant_id=request.tenant_id,
            api_key=api_key,
            api_secret=api_secret,
            resource=intent.entity,
            company=company,
            fields=fields,
            sample_size=intent.requested_records,
            order_by="name desc",
            opener=opener,
        ).discover()

    return run_governed_record_evidence(
        request,
        envelope=envelope,
        understanding=understanding,
        reader=reader,
    )
