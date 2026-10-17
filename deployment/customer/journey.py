"""Bind an offline synthetic read to a freshly checked customer session."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from uuid import UUID

from deployment.release_candidate.continuous_lineage import (
    LineageResult,
    run_synthetic_lineage,
)
from orion.discovery.pilot_read import PilotAuthorization, PilotRequest
from orion_connector_sdk import ConnectorRegistration
from orion_connector_synthetic import SyntheticRecordConnector

from .login import CustomerLogin, LoginDenied


def run_customer_synthetic_lineage(
    *, login: CustomerLogin, session_secret: str,
    registration: ConnectorRegistration, connector: SyntheticRecordConnector,
    request: PilotRequest, authorization_id: str,
    lookup: Callable[[str], PilotAuthorization | None], run_id: UUID,
    private_dir: Path, now: datetime,
) -> LineageResult:
    """Require current tenant membership before the governed read permit."""
    access = login.authorize(session_secret)
    if access.tenant_id != request.tenant_id:
        raise LoginDenied("customer session tenant differs from read scope")
    return run_synthetic_lineage(
        registration=registration, connector=connector, request=request,
        authorization_id=authorization_id, lookup=lookup, run_id=run_id,
        private_dir=private_dir, now=now,
    )
