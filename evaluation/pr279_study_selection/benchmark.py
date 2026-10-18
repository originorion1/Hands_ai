"""Equal-access, label-blind synthetic next-question evaluation.

Only structural seed metadata reaches a selector. The same governed gateway
retrieves one selected schema and one record for each method. Outcome labels
live in a separate file consumed by the scorer after this output is sealed.
"""

from __future__ import annotations

import hashlib
import json
import random
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from orion.contracts import Evidence, EvidenceKind, Observation, ObservationMode
from orion.discovery.governed_runner import run_governed_discovery
from orion.discovery.planner import (
    DiscoveryAuthorization,
    DiscoveryPlan,
    DiscoveryPlanItem,
    DiscoveryTargetKind,
    plan_authorized_discovery,
)
from orion.understanding.metadata import (
    MetadataUnderstanding,
    StructuralEntity,
    StructuralField,
    build_metadata_understanding,
    merge_metadata_understandings,
)

_AT = datetime(2026, 3, 2, tzinfo=UTC)
_METHODS = ("orion", "fixed", "random", "heuristic")


def _json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode()


def _digest(value: object) -> str:
    return hashlib.sha256(_json(value)).hexdigest()


def _root_understanding(case: dict) -> MetadataUnderstanding:
    fields = tuple(StructuralField(
        doctype=case["seed_doctype"], fieldname=row["fieldname"],
        fieldtype=row["fieldtype"], label=None, options=row["options"],
        required=False, read_only=True, hidden=False, unique=False,
    ) for row in case["fields"])
    root = StructuralEntity(
        doctype=case["seed_doctype"], module=None, is_submittable=False,
        is_child_table=False, is_single=False, fields=fields,
        provenance_ids=(uuid5(NAMESPACE_URL, case["case_id"] + ":seed"),),
    )
    return MetadataUnderstanding(case["tenant_id"], (root,))


def _select(method: str, case: dict, current: MetadataUnderstanding,
            authorization: DiscoveryAuthorization) -> DiscoveryPlan:
    if method == "orion":
        return plan_authorized_discovery(current, authorization=authorization)
    allowed = tuple(case["allowed_targets"])
    if method == "fixed":
        target = allowed[0]
    elif method == "random":
        seed = int(hashlib.sha256(case["case_id"].encode()).hexdigest(), 16)
        target = random.Random(seed).choice(allowed)
    elif method == "heuristic":
        ranked = sorted(case["fields"], key=lambda row: (
            row["fieldtype"] not in {"Table", "Table MultiSelect"},
            row["options"],
        ))
        target = ranked[0]["options"]
    else:
        raise ValueError("unknown evaluation method")
    return DiscoveryPlan(case["tenant_id"], (DiscoveryPlanItem(
        DiscoveryTargetKind.METADATA, target,
        "fixed baseline" if method == "fixed" else f"{method} baseline",
        current.entities[0].provenance_ids,
    ),))


def _observation(case: dict, target: str, *, metadata: bool) -> Observation:
    if metadata:
        payload = {"doctype": target, "metadata": {"docs": [{
            "name": target, "fields": [
                {"fieldname": "observed_unit_cost", "fieldtype": "Currency"},
                {"fieldname": "reference_unit_cost", "fieldtype": "Currency"},
                {"fieldname": "quantity", "fieldtype": "Float"},
            ],
        }]}}
        kind = EvidenceKind.METADATA
    else:
        record = case["records"][target]
        payload = {"resource": target, "record": record,
                   "record_sha256": _digest(record)}
        kind = EvidenceKind.API
    evidence = Evidence(
        kind=kind, source=case["source_id"], tenant_id=case["tenant_id"],
        payload=payload, observed_at=_AT,
        evidence_id=uuid5(NAMESPACE_URL, ":".join((
            case["case_id"], target, "metadata" if metadata else "record",
        ))),
    )
    return Observation(evidence=evidence, mode=ObservationMode.READ_ONLY,
                       observation_id=uuid5(NAMESPACE_URL, str(evidence.evidence_id) + ":obs"))


def _conclude(case: dict, observation: Observation) -> dict[str, object]:
    evidence = observation.evidence
    record = evidence.payload["record"]
    result: dict[str, object] = {
        "status": "abstained", "estimated_exposure_usd": None,
        "missing_evidence": [], "alternative_explanations": [],
        "source_record_ref": record["record_ref"],
        "record_sha256": evidence.payload["record_sha256"],
        "evidence_id": str(evidence.evidence_id),
    }
    if (_digest(record) != result["record_sha256"]
            or evidence.tenant_id != case["tenant_id"]
            or evidence.source != case["source_id"]
            or evidence.payload["resource"] not in case["allowed_targets"]):
        result["missing_evidence"] = ["valid source record"]
        return result
    if record["reference_unit_cost"] is None:
        result["missing_evidence"] = ["reference unit cost"]
        return result
    if record["approved_exception"]:
        result["alternative_explanations"] = ["approved exception requires review"]
        return result
    if record["currency"] != "USD" or record["unit"] != "each":
        result["missing_evidence"] = ["reviewed currency and unit conversion"]
        return result
    try:
        observed = Decimal(record["observed_unit_cost"])
        reference = Decimal(record["reference_unit_cost"])
        quantity = Decimal(record["quantity"])
    except (InvalidOperation, TypeError, ValueError):
        result["missing_evidence"] = ["valid price or quantity"]
        return result
    if (not all(value.is_finite() for value in (observed, reference, quantity))
            or reference <= 0 or quantity <= 0 or observed < 0):
        result["missing_evidence"] = ["valid price or quantity"]
        return result
    if observed <= reference:
        result["status"] = "no_anomaly"
        return result
    result["status"] = "candidate_review"
    result["estimated_exposure_usd"] = str(
        ((observed - reference) * quantity).quantize(Decimal("0.01"))
    )
    return result


def run_case(case: dict, method: str) -> dict[str, object]:
    """Execute one blinded choice through the real governed discovery runner."""
    current = _root_understanding(case)
    authorization = DiscoveryAuthorization(
        tenant_id=case["tenant_id"],
        metadata_targets=frozenset(case["allowed_targets"]),
        record_targets=frozenset(case["allowed_targets"]), max_targets=1,
    )
    plan = _select(method, case, current, authorization)
    if len(plan.items) != 1 or plan.items[0].kind is not DiscoveryTargetKind.METADATA:
        raise ValueError("one authorized structural question required")
    selected = plan.items[0].target
    read_requests: list[tuple[str, str]] = []

    def metadata_reader(target: str) -> tuple[Observation, ...]:
        read_requests.append(("metadata", target))
        return (_observation(case, target, metadata=True),)

    def record_reader(target: str) -> tuple[Observation, ...]:
        read_requests.append(("record", target))
        return (_observation(case, target, metadata=False),)

    metadata = run_governed_discovery(
        plan, authorization=authorization, understanding=current,
        metadata_reader=metadata_reader, record_reader=record_reader,
    ).observations[0]
    incremental = build_metadata_understanding(
        (metadata,), tenant_id=case["tenant_id"],
        allowed_doctypes=frozenset({selected}),
    )
    current = merge_metadata_understandings(current, incremental)
    entity = next(entity for entity in current.entities if entity.doctype == selected)
    record_plan = DiscoveryPlan(case["tenant_id"], (DiscoveryPlanItem(
        DiscoveryTargetKind.RECORDS, selected, "selected structure understood",
        entity.provenance_ids,
    ),))
    record = run_governed_discovery(
        record_plan, authorization=authorization, understanding=current,
        metadata_reader=metadata_reader, record_reader=record_reader,
    ).observations[0]
    assert read_requests == [("metadata", selected), ("record", selected)]
    return {
        "case_id": case["case_id"], "domain": case["domain"],
        "method": method, "tenant_id": case["tenant_id"],
        "source_id": case["source_id"], "selected_question": f"Study {selected} structure",
        "selected_target": selected, "rationale": plan.items[0].rationale,
        "structural_evidence_id": str(metadata.evidence.evidence_id),
        "structural_sha256": _digest(metadata.evidence.payload),
        "request_count": len(read_requests), "requests": read_requests,
        "read_only": True, "execution_allowed": False,
        "conclusion": _conclude(case, record),
    }


def run_benchmark(cases_path: Path) -> dict[str, object]:
    source = cases_path.read_bytes()
    data = json.loads(source)
    if data.get("schema") != "orion.study-selection-cases.v1":
        raise ValueError("unexpected study-selection cases")
    predictions = [run_case(case, method) for case in data["cases"]
                   for method in _METHODS]
    return {"schema": "orion.study-selection-predictions.v1",
            "cases_sha256": hashlib.sha256(source).hexdigest(),
            "method_order": list(_METHODS), "predictions": predictions,
            "labels_available_to_selector": False}


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, default=Path(__file__).with_name("cases.json"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(run_benchmark(args.cases), indent=2, sort_keys=True) + "\n")
