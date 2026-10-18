"""Synthetic source rows. Labels live in a separate file and are never imported here."""

from __future__ import annotations

from copy import deepcopy

CASES = {
    "holdout_a": {"target_price": "17", "contract": "11"},
    "holdout_b": {"target_price": "19", "contract": "12", "target_supplier": "supplier-c"},
    "holdout_c": {"target_price": "17", "target_quality": "premium"},
    "holdout_d": {"target_price": "120", "target_pack": "12"},
    "holdout_e": {"target_price": "100", "target_pack": "10", "target_fx": "1.2",
                  "target_currency": "EUR"},
    "holdout_f": {"target_price": "12", "target_tax": "0.20"},
    "holdout_g": {"target_price": "18", "target_delivery": "express"},
    "holdout_h": {"target_price": "16", "contract": None},
    "holdout_i": {"target_price": "18", "duplicate_history": True},
    "holdout_j": {"target_price": "12", "corrected_history": True},
    "holdout_k": {"target_price": "16", "contract": "15"},
    "holdout_l": {"target_price": "11"},
}

FIELDS = (
    "name", "invoice", "company", "item", "supplier", "posting_date", "quantity",
    "units_per_pack", "unit_price", "currency", "fx_to_usd", "tax_rate",
    "discount_rate", "quality", "delivery", "contract_unit_usd", "corrected_by",
)


def rows(case_id: str) -> tuple[dict, ...]:
    """Return fresh source records with no labels or future outcome field."""
    setting = CASES[case_id]
    company = "Synthetic Procurement Co"
    history = []
    for index, price in enumerate(("10", "11", "10"), start=1):
        history.append({
            "name": f"{case_id}-H{index}", "invoice": f"{case_id}-I{index}",
            "company": company, "item": "part-42", "supplier": "supplier-a",
            "posting_date": f"2026-01-0{index}", "quantity": "10",
            "units_per_pack": "1", "unit_price": price, "currency": "USD",
            "fx_to_usd": "1", "tax_rate": "0", "discount_rate": "0",
            "quality": "standard", "delivery": "normal", "contract_unit_usd": None,
            "corrected_by": None,
        })
    if setting.get("duplicate_history"):
        history[1]["invoice"] = history[0]["invoice"]
        history[2]["invoice"] = history[0]["invoice"]
    if setting.get("corrected_history"):
        history[0]["corrected_by"] = history[2]["name"]
    target = deepcopy(history[0])
    target.update({
        "name": f"{case_id}-T", "invoice": f"{case_id}-IT",
        "posting_date": "2026-03-01", "supplier": setting.get("target_supplier", "supplier-b"),
        "unit_price": setting["target_price"],
        "units_per_pack": setting.get("target_pack", "1"),
        "currency": setting.get("target_currency", "USD"),
        "fx_to_usd": setting.get("target_fx", "1"),
        "tax_rate": setting.get("target_tax", "0"),
        "quality": setting.get("target_quality", "standard"),
        "delivery": setting.get("target_delivery", "normal"),
        "contract_unit_usd": setting.get("contract", "11"),
        "corrected_by": None,
    })
    return tuple(history + [target])
