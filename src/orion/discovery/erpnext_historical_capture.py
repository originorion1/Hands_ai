"""Controlled, bounded ERPNext historical capture entry point."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from ..contracts import utc_now
from ..history.evidence import HistoricalEvidenceError
from ..history.sampling import persist_historical_sample
from ..stores.sqlite_historical_evidence import SQLiteHistoricalEvidenceStore
from .erpnext_historical_sample import ERPNextHistoricalSampleAdapter

PURCHASE_INVOICE_RESOURCE = "Purchase Invoice"
FIRST_CAPTURE_SAMPLE_SIZE = 5
FIRST_CAPTURE_FIELDS = (
    "name", "company", "supplier", "posting_date", "currency", "grand_total", "due_date", "docstatus"
)


@dataclass(frozen=True, slots=True)
class ERPNextHistoricalCaptureConfig:
    """In-memory runtime configuration for one bounded capture."""

    base_url: str
    tenant_id: str
    company: str
    api_key: str = field(repr=False)
    api_secret: str = field(repr=False)
    sample_size: int = FIRST_CAPTURE_SAMPLE_SIZE

    def __post_init__(self) -> None:
        for name in ("base_url", "tenant_id", "company", "api_key", "api_secret"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty")
        if self.tenant_id != self.tenant_id.strip():
            raise ValueError("tenant_id must not contain surrounding whitespace")
        if self.company != self.company.strip():
            raise ValueError("company must not contain surrounding whitespace")
        if type(self.sample_size) is not int or self.sample_size != FIRST_CAPTURE_SAMPLE_SIZE:
            raise ValueError("first capture sample_size must be exactly 5")


@dataclass(frozen=True, slots=True)
class ERPNextHistoricalCaptureSummary:
    """Safe aggregate result; no credentials, UUIDs, or customer records."""

    tenant_bound: bool
    resource: str
    batch_sequence: int
    observation_count: int
    persisted: bool
    submitted_only: bool
    company_scope_valid: bool
    erp_writes: int
    execution_allowed: bool


def default_historical_evidence_path(
    tenant_id: str,
    resource: str = PURCHASE_INVOICE_RESOURCE,
    *,
    state_root: str | Path | None = None,
) -> Path:
    """Return stable state outside the repository without raw identifiers."""

    if not isinstance(tenant_id, str) or not tenant_id.strip():
        raise ValueError("tenant_id must be non-empty")
    if not isinstance(resource, str) or not resource.strip():
        raise ValueError("resource must be non-empty")
    root = Path(state_root) if state_root is not None else _default_state_root()
    digest = hashlib.sha256(f"{tenant_id}\0{resource}".encode()).hexdigest()
    return root / f"historical-evidence-{digest}.sqlite3"


def capture_erpnext_historical_sample(
    config: ERPNextHistoricalCaptureConfig,
    *,
    database_path: str | Path | None = None,
    first_capture: bool = True,
    opener: Callable[..., Any] | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> ERPNextHistoricalCaptureSummary:
    """Capture one bounded submitted/company-scoped sample durably."""

    if not isinstance(config, ERPNextHistoricalCaptureConfig):
        raise TypeError("config must be ERPNextHistoricalCaptureConfig")
    path = Path(database_path) if database_path is not None else default_historical_evidence_path(config.tenant_id)
    _reject_repository_destination(path)
    if database_path is None:
        _ensure_state_directory(path.parent)

    store = SQLiteHistoricalEvidenceStore(path)
    history = store.load_all(tenant_id=config.tenant_id, resource=PURCHASE_INVOICE_RESOURCE)
    if first_capture and history:
        raise HistoricalEvidenceError("first historical capture requires empty durable Purchase Invoice history")

    source = ERPNextHistoricalSampleAdapter(
        base_url=config.base_url,
        tenant_id=config.tenant_id,
        api_key=config.api_key,
        api_secret=config.api_secret,
        resource=PURCHASE_INVOICE_RESOURCE,
        company=config.company,
        fields=FIRST_CAPTURE_FIELDS,
        sample_size=config.sample_size,
        opener=opener,
    )
    acknowledgement = persist_historical_sample(
        source, store, tenant_id=config.tenant_id, resource=PURCHASE_INVOICE_RESOURCE, clock=clock
    )
    return ERPNextHistoricalCaptureSummary(
        tenant_bound=True,
        resource=PURCHASE_INVOICE_RESOURCE,
        batch_sequence=acknowledgement.sequence,
        observation_count=acknowledgement.observation_count,
        persisted=True,
        submitted_only=True,
        company_scope_valid=True,
        erp_writes=0,
        execution_allowed=False,
    )


def _default_state_root() -> Path:
    configured = os.environ.get("XDG_STATE_HOME")
    return Path(configured) / "orion" if configured else Path.home() / ".local" / "state" / "orion"


def _ensure_state_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.chmod(0o700)
    except OSError:
        pass


def _reject_repository_destination(path: Path) -> None:
    """Prevent customer evidence databases from being created in the worktree."""

    resolved_path = path.expanduser().resolve(strict=False)
    repository_root = _repository_root()
    if repository_root is None:
        return
    try:
        resolved_path.relative_to(repository_root)
    except ValueError:
        return
    raise ValueError("historical evidence database must be outside the Git repository")


def _repository_root() -> Path | None:
    """Find the current worktree root without inspecting runtime credentials."""

    candidates = (Path(__file__).resolve().parent, Path.cwd().resolve())
    for candidate in candidates:
        for parent in (candidate, *candidate.parents):
            if (parent / ".git").exists():
                return parent
    return None
