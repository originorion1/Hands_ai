"""Fixed, bounded Purchase Invoice accounting-v1 rich evidence capture."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request

from ..contracts import Evidence, EvidenceKind, Observation, ObservationMode, utc_now
from ..history.evidence import HistoricalEvidenceError
from ..history.profiled_evidence import ProfiledEvidenceBatch
from ..stores.sqlite_historical_evidence import SQLiteHistoricalEvidenceStore
from ..stores.sqlite_profiled_evidence import SQLiteProfiledEvidenceStore
from .erpnext_adapter import _default_opener, _normalize_base_url
from .erpnext_historical_capture import (
    FIRST_CAPTURE_FIELDS,
    PURCHASE_INVOICE_RESOURCE,
    _reject_repository_destination,
)
from .erpnext_historical_expansion import _validate_history_unique

PROFILE_ID = "purchase-invoice-accounting-v1"
PARENT_FIELDS = FIRST_CAPTURE_FIELDS + ("credit_to", "cost_center", "project", "payment_terms_template", "taxes_and_charges", "purchase_order", "set_warehouse")
ITEM_FIELDS = ("item_code", "item_name", "expense_account", "cost_center", "project", "warehouse", "purchase_order", "purchase_receipt", "qty", "rate", "amount", "item_tax_template")
TAX_FIELDS = ("account_head", "add_deduct_tax", "charge_type", "cost_center", "rate", "tax_amount", "included_in_print_rate")


@dataclass(frozen=True, slots=True)
class RichCaptureConfig:
    base_url: str
    tenant_id: str
    company: str
    api_key: str = field(repr=False)
    api_secret: str = field(repr=False)

    def __post_init__(self) -> None:
        for value in (self.base_url, self.tenant_id, self.company, self.api_key, self.api_secret):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("rich capture configuration values must be non-empty")
        if not self.tenant_id == self.tenant_id.strip() or not self.company == self.company.strip():
            raise ValueError("rich capture scope values must not have surrounding whitespace")
        normalized = _normalize_base_url(self.base_url)
        if not normalized.lower().startswith("https://"):
            raise ValueError("rich capture requires HTTPS")


@dataclass(frozen=True, slots=True)
class RichCaptureSummary:
    tenant_bound: bool
    resource: str
    profile_id: str
    selected_invoice_count: int
    fetched_invoice_count: int
    persisted_invoice_count: int
    total_item_row_count: int
    total_tax_row_count: int
    profile_batch_sequence: int
    profile_batch_count: int
    profile_observation_count: int
    durable_profile_verified: bool
    header_history_mutated: bool
    erp_writes: int
    recommendation_allowed: bool
    promotion_allowed: bool
    execution_allowed: bool


def capture_purchase_invoice_rich_v1(
    config: RichCaptureConfig,
    *,
    header_database_path: str | Path,
    profile_database_path: str | Path,
    opener: Callable[..., object] | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> RichCaptureSummary:
    if not isinstance(config, RichCaptureConfig):
        raise TypeError("config must be RichCaptureConfig")
    _reject_repository_destination(Path(header_database_path))
    _reject_repository_destination(Path(profile_database_path))
    header_store = SQLiteHistoricalEvidenceStore(header_database_path)
    history = header_store.load_all(tenant_id=config.tenant_id, resource=PURCHASE_INVOICE_RESOURCE)
    _validate_history_unique(history, config.tenant_id)
    if len(history) != 3 or tuple(batch.sequence for batch in history) != (1, 2, 3) or sum(len(batch.observations) for batch in history) != 100:
        raise HistoricalEvidenceError("rich capture requires exactly 3 header batches and 100 observations")
    profile_store = SQLiteProfiledEvidenceStore(profile_database_path)
    if profile_store.load_all(tenant_id=config.tenant_id, resource=PURCHASE_INVOICE_RESOURCE, profile_id=PROFILE_ID):
        raise HistoricalEvidenceError("rich profile already captured")
    candidates = []
    for batch in history:
        for observation in batch.observations:
            record = observation.evidence.payload["record"]
            try:
                posting = date.fromisoformat(record["posting_date"])
            except (KeyError, TypeError, ValueError) as exc:
                raise HistoricalEvidenceError("header posting date is invalid") from exc
            candidates.append((posting, record["name"], record))
    selected = sorted(candidates, key=lambda item: (item[0], item[1]), reverse=True)[:10]
    if len(selected) != 10:
        raise HistoricalEvidenceError("rich capture requires 10 selectable invoices")
    adapter = _PrivateRichTransport(config, opener)
    observations: list[Observation] = []
    item_count = tax_count = 0
    for _, name, _ in selected:
        projected, items, taxes = adapter.fetch(name)
        observations.append(Observation(Evidence(EvidenceKind.API, "erpnext-purchase-invoice-accounting-v1-read-only", {"resource": PURCHASE_INVOICE_RESOURCE, "profile_id": PROFILE_ID, "record": projected}, tenant_id=config.tenant_id), ObservationMode.READ_ONLY))
        item_count += items
        tax_count += taxes
    batch = ProfiledEvidenceBatch(config.tenant_id, PURCHASE_INVOICE_RESOURCE, PROFILE_ID, 1, clock(), tuple(observations))
    profile_store.append(batch)
    reloaded = profile_store.load_all(tenant_id=config.tenant_id, resource=PURCHASE_INVOICE_RESOURCE, profile_id=PROFILE_ID)
    if len(reloaded) != 1 or len(reloaded[0].observations) != 10:
        raise HistoricalEvidenceError("rich profile reload verification failed")
    return RichCaptureSummary(True, PURCHASE_INVOICE_RESOURCE, PROFILE_ID, 10, 10, 10, item_count, tax_count, 1, 1, 10, True, False, 0, False, False, False)


class _PrivateRichTransport:
    def __init__(self, config: RichCaptureConfig, opener: Callable[..., object] | None) -> None:
        self._config = config
        self._opener = opener or _default_opener

    def fetch(self, name: str) -> tuple[dict[str, object], int, int]:
        url = f"{self._config.base_url.rstrip('/')}/api/resource/{quote(PURCHASE_INVOICE_RESOURCE, safe='')}/{quote(name, safe='')}"
        request = Request(url, headers={"Authorization": f"token {self._config.api_key}:{self._config.api_secret}", "Accept": "application/json"}, method="GET")
        with self._opener(request, timeout=30) as response:
            final_url = getattr(response, "geturl", lambda: request.full_url)()
            if final_url != request.full_url:
                raise HistoricalEvidenceError("rich capture redirects are not allowed")
            body = response.read(1_000_001)
        if len(body) > 1_000_000:
            raise HistoricalEvidenceError("rich capture response exceeds bound")
        try:
            data = json.loads(body.decode())
            document = data["data"]
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise HistoricalEvidenceError("rich capture response is invalid") from exc
        if not isinstance(document, dict) or document.get("name") != name or document.get("company") != self._config.company or type(document.get("docstatus")) is not int or document.get("docstatus") != 1:
            raise HistoricalEvidenceError("rich capture document audit fields are invalid")
        if not all(field in document for field in PARENT_FIELDS) or not isinstance(document.get("items"), list) or not isinstance(document.get("taxes"), list):
            raise HistoricalEvidenceError("rich capture document projection is invalid")
        if not document["items"] or len(document["items"]) > 50 or len(document["taxes"]) > 20:
            raise HistoricalEvidenceError("rich capture child row bounds are invalid")
        for context_field in ("supplier", "posting_date", "currency", "credit_to"):
            if not isinstance(document[context_field], str) or not document[context_field].strip():
                raise HistoricalEvidenceError("rich capture parent context is invalid")
        projected = {field: document[field] for field in PARENT_FIELDS}
        projected["items"] = [_project_row(row, ITEM_FIELDS) for row in document["items"]]
        projected["taxes"] = [_project_row(row, TAX_FIELDS) for row in document["taxes"]]
        return projected, len(projected["items"]), len(projected["taxes"])


def _project_row(row: object, fields: tuple[str, ...]) -> dict[str, object]:
    if not isinstance(row, Mapping):
        raise HistoricalEvidenceError("rich capture child row is invalid")
    if not isinstance(row.get("item_name"), str) and "item_name" in fields:
        raise HistoricalEvidenceError("rich capture item_name is required")
    for numeric_field in ("qty", "rate", "amount"):
        if numeric_field in fields and (type(row.get(numeric_field)) not in {int, float} or isinstance(row.get(numeric_field), bool)):
            raise HistoricalEvidenceError("rich capture numeric child field is invalid")
    for text_field in ("account_head", "add_deduct_tax", "charge_type"):
        if text_field in fields and (not isinstance(row.get(text_field), str) or not row[text_field].strip()):
            raise HistoricalEvidenceError("rich capture tax context is invalid")
    return {field: row.get(field) for field in fields}
