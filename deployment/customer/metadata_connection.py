"""Bind an approved metadata grant to one active, enrolled vault reference.

The vault callback is supplied by the reviewed server-side deployment. Raw
credentials exist only in the short-lived environment mapping passed to the
bounded runner. Each lookup and commit rechecks tenant, company, endpoint,
external reference and credential generation against the private registry.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .bounded_metadata import (
    MetadataGrant,
    MetadataSessionDenied,
    MetadataSessionResult,
    run_bounded_metadata_session,
)
from .metadata_approval import ApprovedMetadataGrantLedger
from .metadata_study_report import MetadataStudyReportSource, persist_metadata_study
from .metadata_transport import ReviewedMetadataTransport
from .tenants import TenantRegistry


@dataclass(frozen=True, slots=True)
class EnrolledMetadataConnection:
    registry: TenantRegistry
    ledger: ApprovedMetadataGrantLedger
    label: str
    vault_read: Callable[[str], tuple[str, str]] = field(repr=False)
    _scope_db: ContextVar = field(
        default_factory=lambda: ContextVar("orion_enrolled_metadata_scope", default=None),
        init=False, repr=False, compare=False,
    )

    def __post_init__(self) -> None:
        if (not isinstance(self.registry, TenantRegistry)
                or not isinstance(self.ledger, ApprovedMetadataGrantLedger)
                or type(self.label) is not str or not self.label
                or not callable(self.vault_read)
                or not self.ledger.grant.credential_vault_reference):
            raise MetadataSessionDenied("reviewed enrolled metadata connection required")
        with self.registry._connection() as db:
            self._require_current(db)

    def _require_current(self, db) -> None:
        grant = self.ledger.grant
        row = db.execute(
            "SELECT c.credential_ref,c.status,c.connector,p.endpoint,p.company,"
            "e.company_reference,t.status FROM credential_refs c "
            "JOIN connection_profiles p ON p.tenant_id=c.tenant_id AND p.label=c.label "
            "JOIN tenant_external_references e ON e.tenant_id=c.tenant_id "
            "JOIN tenants t ON t.tenant_id=c.tenant_id "
            "WHERE c.tenant_id=? AND c.label=?",
            (grant.tenant_id, self.label),
        ).fetchone()
        if row != (
            grant.credential_vault_reference, "active", "erpnext", grant.origin,
            grant.company, grant.external_customer_reference, "active",
        ):
            raise MetadataSessionDenied("enrolled credential or company scope changed")

    def environment(self) -> dict[str, str]:
        """Resolve only the currently enrolled reference on the server side."""
        with self.registry._connection() as db:
            self._require_current(db)
        try:
            values = self.vault_read(self.ledger.grant.credential_vault_reference)
        except Exception:  # noqa: BLE001 - external vault errors must not leak references
            raise MetadataSessionDenied("vault credentials unavailable") from None
        with self.registry._connection() as db:
            self._require_current(db)
        if (type(values) is not tuple or len(values) != 2
                or any(type(value) is not str or not value for value in values)
                or values[0] == values[1]):
            raise MetadataSessionDenied("vault credentials unavailable")
        references = self.ledger.grant.credential_references
        return {references.api_key_variable: values[0],
                references.api_secret_variable: values[1]}

    def lookup(self, authorization_id: str) -> MetadataGrant | None:
        if authorization_id != self.ledger.grant.authorization_id:
            return None
        scoped = self._scope_db.get()
        if scoped is not None:
            try:
                self._require_current(scoped)
            except MetadataSessionDenied:
                return None
            # commit_scope holds both registry and grant revocation locks.
            return self.ledger.grant
        with self.registry._connection() as db:
            try:
                self._require_current(db)
            except MetadataSessionDenied:
                return None
        return self.ledger.lookup(authorization_id)

    @contextmanager
    def commit_scope(self, authorization_id: str) -> Iterator[None]:
        with self.registry._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_current(db)
            with self.ledger.commit_scope(authorization_id):
                token = self._scope_db.set(db)
                try:
                    self._require_current(db)
                    yield
                finally:
                    self._scope_db.reset(token)

    def report_active(self, authorization_id: str, *, now: datetime) -> bool:
        return self.ledger.report_active(authorization_id, now=now)

    @contextmanager
    def report_commit_scope(self, authorization_id: str, *, now: datetime) -> Iterator[None]:
        with self.ledger.report_commit_scope(authorization_id, now=now):
            yield

    def require_current_for_report(self, registry_db: object) -> None:
        """Called inside CustomerLogin's registry transaction, without reopening it."""
        self._require_current(registry_db)


def run_enrolled_metadata_session(
    connection: EnrolledMetadataConnection, *, transport: Callable[..., object],
    state_directory: Path, clock: Callable[[], datetime],
    monotonic: Callable[[], float] = time.monotonic,
) -> MetadataSessionResult:
    """One-use qualified launch; interruption consumes the signed approval."""
    result, _ = _run_enrolled(
        connection, transport=transport, state_directory=state_directory,
        clock=clock, monotonic=monotonic, persist_study=False,
    )
    return result


def run_confined_metadata_session(
    connection: EnrolledMetadataConnection, *, state_directory: Path,
    clock: Callable[[], datetime], ca_bundle: Path | None = None,
    monotonic: Callable[[], float] = time.monotonic,
) -> tuple[MetadataSessionResult, MetadataStudyReportSource]:
    """Qualified one-use launch and same-grant persisted structural report."""
    if not isinstance(connection, EnrolledMetadataConnection):
        raise MetadataSessionDenied("enrolled metadata connection required")
    transport = ReviewedMetadataTransport(connection.ledger.grant.origin, ca_bundle)
    return run_enrolled_metadata_study(
        connection, transport=transport, state_directory=state_directory,
        clock=clock, monotonic=monotonic,
    )


def run_enrolled_metadata_study(
    connection: EnrolledMetadataConnection, *, transport: Callable[..., object],
    state_directory: Path, clock: Callable[[], datetime],
    monotonic: Callable[[], float] = time.monotonic,
) -> tuple[MetadataSessionResult, MetadataStudyReportSource]:
    """Synthetic transport seam; persist under the still-active grant lock."""
    result, source = _run_enrolled(
        connection, transport=transport, state_directory=state_directory,
        clock=clock, monotonic=monotonic, persist_study=True,
    )
    assert source is not None
    return result, source


def _run_enrolled(
    connection: EnrolledMetadataConnection, *, transport: Callable[..., object],
    state_directory: Path, clock: Callable[[], datetime], monotonic: Callable[[], float],
    persist_study: bool,
) -> tuple[MetadataSessionResult, MetadataStudyReportSource | None]:
    if not isinstance(connection, EnrolledMetadataConnection):
        raise MetadataSessionDenied("enrolled metadata connection required")
    connection.ledger.claim()
    complete = False
    try:
        result = run_bounded_metadata_session(
            connection.ledger.grant, lookup=connection.lookup,
            environment=connection.environment(), transport=transport,
            commit_scope=connection.commit_scope, state_directory=state_directory,
            clock=clock, monotonic=monotonic,
        )
        source = None
        if persist_study:
            with connection.commit_scope(connection.ledger.grant.authorization_id):
                if connection.lookup(connection.ledger.grant.authorization_id) is None:
                    raise MetadataSessionDenied("metadata authorization revoked")
                _, digest = persist_metadata_study(
                    grant=connection.ledger.grant, state_directory=state_directory,
                )
            source = MetadataStudyReportSource(connection.ledger.grant, state_directory, digest)
        complete = True
        return result, source
    finally:
        connection.ledger.finish(complete=complete)
