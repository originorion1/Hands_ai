"""Deterministic, evidence-scoped procurement price investigation.

Inputs are admitted purchase records. This module cannot acquire evidence or
grant authority; callers retain the Kernel's read permit and custody checks.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from statistics import median


class ProcurementEvidenceError(ValueError):
    """A purchase record cannot support financial arithmetic."""


def _decimal(value: object, *, positive: bool = False) -> Decimal:
    if type(value) is not str or len(value) > 40:
        raise ProcurementEvidenceError("decimal source value required")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ProcurementEvidenceError("invalid decimal source value") from exc
    if (not number.is_finite() or number > Decimal("1e12") or
            (positive and number < Decimal("1e-9")) or
            (not positive and number < 0)):
        raise ProcurementEvidenceError("invalid nonnegative source value")
    return number


def _money(value: Decimal) -> str:
    if not value.is_finite() or value < 0 or value > Decimal("1e15"):
        raise ProcurementEvidenceError("financial exposure exceeds bound")
    try:
        return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    except InvalidOperation as exc:
        raise ProcurementEvidenceError("financial arithmetic unavailable") from exc


@dataclass(frozen=True, slots=True)
class Purchase:
    ref: str
    invoice: str
    company: str
    item: str
    supplier: str
    day: date
    quantity: Decimal
    units_per_pack: Decimal
    unit_price: Decimal
    fx_to_usd: Decimal
    tax_rate: Decimal
    discount_rate: Decimal
    quality: str
    delivery: str
    currency: str
    contract_unit_usd: Decimal | None
    corrected_by: str | None

    @classmethod
    def from_record(cls, row: Mapping[str, object]) -> Purchase:
        required = {
            "name", "invoice", "company", "item", "supplier", "posting_date",
            "quantity", "units_per_pack", "unit_price", "currency", "fx_to_usd",
            "tax_rate", "discount_rate", "quality", "delivery", "contract_unit_usd",
            "corrected_by",
        }
        if not isinstance(row, Mapping) or set(row) != required:
            raise ProcurementEvidenceError("purchase record field scope mismatch")
        text_fields = ("name", "invoice", "company", "item", "supplier", "currency",
                       "quality", "delivery")
        if any(type(row[key]) is not str or not row[key] or len(row[key]) > 128
               for key in text_fields):
            raise ProcurementEvidenceError("invalid purchase identity")
        if (row["corrected_by"] is not None and
                (type(row["corrected_by"]) is not str or not row["corrected_by"])):
            raise ProcurementEvidenceError("invalid correction reference")
        try:
            day = date.fromisoformat(row["posting_date"])
        except (TypeError, ValueError) as exc:
            raise ProcurementEvidenceError("invalid purchase date") from exc
        tax = _decimal(row["tax_rate"])
        discount = _decimal(row["discount_rate"])
        if tax > 1 or discount >= 1:
            raise ProcurementEvidenceError("invalid tax or discount")
        contract = (None if row["contract_unit_usd"] is None else
                    _decimal(row["contract_unit_usd"], positive=True))
        currency = row["currency"]
        fx = _decimal(row["fx_to_usd"], positive=True)
        if currency not in {"USD", "EUR"} or (currency == "USD" and fx != 1):
            raise ProcurementEvidenceError("unsupported currency conversion")
        return cls(
            row["name"], row["invoice"], row["company"], row["item"],
            row["supplier"], day, _decimal(row["quantity"], positive=True),
            _decimal(row["units_per_pack"], positive=True),
            _decimal(row["unit_price"], positive=True),
            fx, tax, discount,
            row["quality"], row["delivery"], currency, contract,
            row["corrected_by"],
        )

    @property
    def normalized_unit_usd(self) -> Decimal:
        return (self.unit_price * (1 - self.discount_rate) * self.fx_to_usd /
                ((1 + self.tax_rate) * self.units_per_pack))

    @property
    def base_units(self) -> Decimal:
        return self.quantity * self.units_per_pack


def investigate_purchases(
    records: tuple[Mapping[str, object], ...], *, target_ref: str,
    company: str, threshold: Decimal = Decimal("0.15"),
) -> dict[str, object]:
    """Return a reviewer candidate or an explicit abstention with reasons.

    Price is normalized before tax. A reference uses the higher of a stated
    contract and the historical median, limiting speculative exposure. A
    target requires two distinct comparable prior invoices within 90 days.
    """
    if not records or len(records) > 100 or threshold <= 0 or threshold >= 1:
        raise ProcurementEvidenceError("bounded investigation required")
    purchases = tuple(Purchase.from_record(row) for row in records)
    refs = [row.ref for row in purchases]
    if len(refs) != len(set(refs)):
        raise ProcurementEvidenceError("duplicate source record reference")
    matches = [row for row in purchases if row.ref == target_ref]
    if len(matches) != 1 or matches[0].company != company:
        raise ProcurementEvidenceError("target company or record mismatch")
    target = matches[0]
    prior = [row for row in purchases if row.company == company and row.item == target.item
             and row.day < target.day and 0 < (target.day - row.day).days <= 90]
    reasons = []
    comparable = []
    for row in prior:
        if row.corrected_by:
            reasons.append(f"{row.ref}: superseded by {row.corrected_by}")
        elif row.quality != target.quality or row.delivery != target.delivery:
            reasons.append(f"{row.ref}: quality or delivery differs")
        else:
            comparable.append(row)
    unique_invoices = {row.invoice for row in comparable}
    if len(unique_invoices) != len(comparable):
        reasons.append("duplicate invoice among comparisons")
        comparable = [row for row in comparable if sum(
            other.invoice == row.invoice for other in comparable
        ) == 1]
    missing = []
    if target.contract_unit_usd is None:
        missing.append("independently verified contract terms")
    if len(comparable) < 2:
        missing.append("two distinct comparable historical invoices")
    base = {
        "target_ref": target.ref, "company": company, "item": target.item,
        "normalized_unit": "each", "currency": "USD", "quantity_each": str(target.base_units),
        "target_unit_usd": _money(target.normalized_unit_usd),
        "comparison_refs": [row.ref for row in comparable],
        "excluded_evidence": reasons, "missing_evidence": missing,
        "method": "tax-inclusive input to pre-tax net unit USD; 90-day same-item/quality/delivery median; "
                  "higher of median and stated contract; 15% threshold",
        "alternative_explanations": [
            "quality and delivery differences excluded",
            "tax and discount normalized",
            "currency conversion relies on acquired FX field",
            "market price change requires reviewer assessment",
        ],
        "estimated_exposure_usd": "0.00", "reviewer_decision": "pending",
        "independently_verified_savings_usd": None,
        "invalidation_history": [],
    }
    if target.corrected_by:
        base["excluded_evidence"].append(
            f"{target.ref}: superseded by {target.corrected_by}"
        )
        return base | {"status": "abstained", "confidence": "stale_source_record",
                       "next_question": "Acquire the corrected target under a new read permit"}
    if len(comparable) < 2:
        return base | {"status": "abstained", "confidence": "insufficient_evidence",
                       "next_question": "Acquire two permitted comparable invoices or abstain"}
    center = median(row.normalized_unit_usd for row in comparable)
    reference = max(center, target.contract_unit_usd or Decimal(0))
    base["historical_median_unit_usd"] = _money(center)
    base["reference_unit_usd"] = _money(reference)
    base["supplier_comparison"] = sorted({row.supplier for row in comparable})
    if target.normalized_unit_usd <= reference * (1 + threshold):
        return base | {"status": "no_anomaly", "confidence": "bounded_evidence",
                       "next_question": None}
    exposure = (target.normalized_unit_usd - reference) * target.base_units
    base["estimated_exposure_usd"] = _money(exposure)
    return base | {"status": "candidate_review", "confidence": "limited_synthetic",
                   "next_question": ("Verify contract and market terms with an authorized reviewer"
                                     if missing else "Ask reviewer to assess market change")}
