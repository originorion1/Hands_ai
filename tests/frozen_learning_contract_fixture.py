"""Self-authored package used only to verify the frozen-evaluation bridge."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

from fnb_lab import Restaurant


def _opaque(prefix: str, label: str) -> str:
    return f"{prefix}_{hashlib.sha256(label.encode()).hexdigest()[:24]}"


def _source(label: str) -> str:
    return f"https://s-{hashlib.sha256(label.encode()).hexdigest()[:24]}.synthetic.test"


def _batch(lab, resource_index, records, date_field, label, company):
    return {
        "resource_id": lab.resources[resource_index],
        "authorization_id": _opaque("g", f"{label}:authorization"),
        "provenance_source": _opaque("p", f"{label}:provenance"),
        "identity_field": lab.identity,
        "company_field": lab.partition,
        "date_field": date_field,
        "records": records,
    }


def _environment(label: str, *, seed: int, tenant: str, company: str, header_detail: bool):
    source = _source(f"{label}:organization")
    lab = Restaurant(
        seed=seed, tenant=tenant, source=source, header_detail=header_detail,
    ).populate()
    resources = []
    for index, resource in enumerate(lab.resources):
        records = [dict(zip(
            (*lab.columns[index], lab.identity, lab.partition),
            (*values, f"p_{row}" if index == lab.product_index else f"x_{row}", company),
            strict=True,
        )) for row, values in enumerate(lab.values[index])]
        date_field = lab.columns[index][lab.kinds[index].index("Date")]
        resources.append({
            "id": resource,
            "fields": [
                {"id": field, "kind": kind}
                for field, kind in zip(lab.columns[index], lab.kinds[index], strict=True)
            ],
            "historical_batch": _batch(
                lab, index, records, date_field, f"{label}:history:{index}", company,
            ),
        })
    instruments = []
    for instrument_index, declared in enumerate(lab.instruments):
        observations = tuple(
            observation
            for batch_index, batch in enumerate(lab.anchor_batches)
            if batch_index % len(lab.instruments) == instrument_index
            for observation in batch
        )
        records = []
        origins = []
        for observation in observations:
            record = dict(observation.evidence.payload["record"])
            if record["channel"] not in {
                "gross_sales", "served_units", "business_event_date",
                "served_item", "sales_header",
            } or record["subject_resource"] not in set(
                lab.resources[:2] if header_detail else lab.resources[:1]
            ):
                continue
            record["partition"] = company
            records.append(record)
            origin = lab.archive[("origin", observation.evidence.evidence_id)]
            origins.append({
                "record_id": record["id"],
                "roots": [list(root) for root in origin.roots],
                "parents": [],
            })
        instruments.append({
            "source_id": _source(f"{label}:instrument:{instrument_index}"),
            "resource_id": _opaque("r", f"{label}:instrument:{instrument_index}"),
            "provenance_source": _opaque("p", f"{label}:instrument:{instrument_index}"),
            "classes": list(declared.classes),
            "authorization_id": _opaque("g", f"{label}:instrument:{instrument_index}"),
            "records": records,
            "origins": origins,
        })
    return lab, {
        "source_id": source,
        "metadata_authorization_id": _opaque("g", f"{label}:metadata"),
        "resources": resources,
        "instruments": instruments,
    }


def _release(
    label: str, case_id: str, available_at: datetime, lab, company: str,
    parts: tuple[int, int], *, header_detail: bool,
):
    occurred = available_at.date().isoformat()
    if not header_detail:
        records = [{
            lab.identity: f"{case_id}-row-{index}", lab.partition: company,
            lab.columns[0][0]: value, lab.columns[0][2]: occurred,
        } for index, value in enumerate(parts, 1)]
        batches = [_batch(
            lab, 0, records, lab.columns[0][2], f"{label}:{case_id}:flat", company,
        )]
    else:
        header_id = f"{case_id}-header"
        header_records = [{
            lab.identity: header_id, lab.partition: company,
            lab.columns[0][0]: occurred,
        }]
        detail_records = [{
            lab.identity: f"{case_id}-detail-{index}", lab.partition: company,
            lab.columns[1][0]: value, lab.columns[1][2]: header_id,
            lab.columns[1][-1]: occurred,
        } for index, value in enumerate(parts, 1)]
        batches = [
            _batch(
                lab, 0, header_records, lab.columns[0][0],
                f"{label}:{case_id}:header", company,
            ),
            _batch(
                lab, 1, detail_records, lab.columns[1][-1],
                f"{label}:{case_id}:detail", company,
            ),
        ]
    return {
        "case_id": case_id,
        "available_at": available_at.isoformat(),
        "source_id": _source(f"{label}:outcomes"),
        "batches": batches,
    }


def refresh_dataset_identity(package: dict) -> dict:
    staged = {
        key: package[key]
        for key in (
            "timeline", "learner_inputs", "outcome_releases", "evaluator_only_commitment"
        )
    }
    material = json.dumps(
        staged, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()
    package["dataset_id"] = "dataset-" + hashlib.sha256(material).hexdigest()[:32]
    return package


def self_authored_package(protocol_sha256: str) -> dict:
    tenant = _opaque("t", "bridge:tenant")
    company = _opaque("c", "bridge:company")
    development_lab, development = _environment(
        "development", seed=17, tenant=tenant, company=company, header_detail=False,
    )
    evaluation_lab, evaluation = _environment(
        "evaluation", seed=18, tenant=tenant, company=company, header_detail=True,
    )
    start = datetime(2031, 1, 1, tzinfo=UTC)
    package = {
        "package_version": "orion-independent-restaurant-dataset-v1",
        "dataset_id": "pending",
        "protocol": {
            "version": "orion-frozen-learning-evaluation-v1",
            "sha256": protocol_sha256,
        },
        "authorship": {
            "prepared_by": "ORION bridge test authors",
            "preparer_role": "evaluation-tool implementers",
            "prepared_at": "2030-12-29T00:00:00+00:00",
            "fixed_at": "2030-12-31T00:00:00+00:00",
            "independence_basis": "None; this is a self-authored infrastructure fixture.",
            "engine_source_seen_before_fixing": True,
            "engine_source_seen_details": "The frozen learner and its prior tests were visible.",
            "engine_results_seen_before_fixing": True,
            "engine_results_seen_details": "Prior self-authored learning-cycle results were visible.",
            "preparer_is_engine_implementer": True,
            "shared_fixture_engine_authorship": True,
            "answers_fixed_before_execution": True,
            "learner_visible_material": ["currently released opaque records and evidence"],
            "evaluator_retained_material": ["future releases and commitment digest"],
            "review": {
                "status": "NOT_PROVEN",
                "reviewed_by": "no independent reviewer",
                "reviewer_role": "none",
                "reviewed_at": start.isoformat(),
                "evidence_sha256": "0" * 64,
            },
        },
        "timeline": {
            "discovery_evidence_cutoff": "2030-12-30T00:00:00+00:00",
            "evaluation_clock_start": start.isoformat(),
        },
        "learner_inputs": {
            "tenant_id": tenant, "company_id": company,
            "development": development, "evaluation": evaluation,
        },
        "outcome_releases": {
            "development": [
                _release(
                    "development", "development-1", start + timedelta(days=1),
                    development_lab, company, (70, 80), header_detail=False,
                ),
                _release(
                    "development", "development-2", start + timedelta(days=2),
                    development_lab, company, (60, 70), header_detail=False,
                ),
            ],
            "evaluation": [
                _release(
                    "evaluation", "evaluation-1", start + timedelta(days=3),
                    evaluation_lab, company, (40, 60), header_detail=True,
                ),
                _release(
                    "evaluation", "evaluation-2", start + timedelta(days=4),
                    evaluation_lab, company, (75, 65), header_detail=True,
                ),
            ],
        },
        "evaluator_only_commitment": {
            "sealed_expected_results_sha256": hashlib.sha256(
                b"self-authored expected bridge results"
            ).hexdigest(),
            "held_by": "self-authored trusted test evaluator",
            "economic_value_criterion": None,
        },
    }
    return refresh_dataset_identity(package)
