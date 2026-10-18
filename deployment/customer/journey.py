"""Bind an offline synthetic read to a freshly checked customer session."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
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
    first_grant: list[PilotAuthorization | None] = []

    def session_bound_lookup(identity: str) -> PilotAuthorization | None:
        current = login.authorize(session_secret)
        if current.tenant_id != request.tenant_id:
            raise LoginDenied("customer session tenant differs from read scope")
        grant = lookup(identity)
        if first_grant and grant != first_grant[0]:
            raise LoginDenied("customer read authorization changed")
        if not first_grant:
            first_grant.append(grant)
        return grant

    def persistence_guard() -> None:
        if not isinstance(session_bound_lookup(authorization_id), PilotAuthorization):
            raise LoginDenied("customer read authorization unavailable")

    @contextmanager
    def commit_scope():
        with login.authorized_report_commit(session_secret, tenant_id=request.tenant_id):
            # The session is locked against logout and tenant lifecycle changes.
            # The grant remains an independent, trusted control-plane decision.
            grant = lookup(authorization_id)
            if not isinstance(grant, PilotAuthorization) or grant != first_grant[0]:
                raise LoginDenied("customer read authorization changed")
            yield

    return run_synthetic_lineage(
        registration=registration, connector=connector, request=request,
        authorization_id=authorization_id, lookup=session_bound_lookup, run_id=run_id,
        private_dir=private_dir, now=now,
        persistence_guard=persistence_guard, commit_scope=commit_scope,
    )
