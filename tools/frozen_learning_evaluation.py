#!/usr/bin/env python3
"""Preflight frozen ORION evaluation material and execute supported V2 packages.

This utility verifies the frozen learner and an evaluator-supplied package. A
trusted local controller translates package mechanics into bounded synthetic
authorizations; package authorization identifiers are references, never grants.
Future releases stay evaluator-side until durable prediction commitments match.
V1 remains available for validation and explicit conversion, but cannot run an
independent evaluation. No package-supplied code is imported or executed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from orion.business.fnb import FNB_RULES, assess_restaurant
from orion.business.fnb import VERSION as FNB_VERSION
from orion.contracts import Evidence, EvidenceKind, Observation
from orion.discovery.pilot_metadata import (
    MetadataAuthorization,
    MetadataRequest,
    ScopeProposal,
    launch_pilot_metadata,
)
from orion.discovery.pilot_read import (
    PilotAuthorization,
    PilotRequest,
    launch_pilot_read,
)
from orion.discovery.read_window import ReviewedReadWindow
from orion.learning.organizational_cycle import (
    BASELINE_MODEL,
    HORIZON,
    MAX_OUTCOME_RECORDS,
    REVISED_MODEL,
    begin_learning_cycle,
    resume_learning_cycle,
    select_prediction_question,
)
from orion.learning.prediction_ledger import BusinessCohort
from orion.understanding.role_study import RoleStudy
from orion.understanding.schema_evidence import FieldDeclaration, interpret_schema
from orion.understanding.semantic_study import (
    AGGREGATE_FIELDS,
    ANCHOR_FIELDS,
    SEMANTIC_EVALUATOR_VERSION,
    Instrument,
    Origin,
    SemanticStudy,
)

PROTOCOL_VERSION = "orion-frozen-learning-evaluation-v1"
PACKAGE_VERSION = "orion-independent-restaurant-dataset-v1"
REVIEW_VERSION = "orion-independent-review-evidence-v1"
PROTOCOL_VERSION_V2 = "orion-frozen-learning-evaluation-v2"
PACKAGE_VERSION_V2 = "orion-independent-restaurant-dataset-v2"
REVIEW_VERSION_V2 = "orion-independent-review-evidence-v2"
FREEZE_RECEIPT_VERSION_V2 = "orion-evaluator-freeze-receipt-v2"
V1_VALIDATION_ONLY_STATUS = "VALIDATED_V1_MATERIAL_EXECUTION_UNSUPPORTED"
SUPPORTED_PROTOCOLS = frozenset({PROTOCOL_VERSION, PROTOCOL_VERSION_V2})
OPAQUE = re.compile(r"^[a-z]_[a-f0-9]{16,64}$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")
SYNTHETIC_ORIGIN = re.compile(r"^s-[a-f0-9]{16,64}\.synthetic\.test$")
FORBIDDEN_RECORD_KEYS = {
    "actual",
    "answer",
    "business_role",
    "expected",
    "expected_result",
    "field_mapping",
    "label",
    "precomputed_total",
    "semantic_mapping",
    "target_total",
}


class ContractError(ValueError):
    """An external evaluation input violates the preregistered contract."""


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest_file(path: Path) -> str:
    return _digest_bytes(path.read_bytes())


def _material_projection(package: Mapping[str, object]) -> dict[str, object]:
    """Return the immutable learner/evaluator material, excluding its envelope."""
    return {
        "timeline": package["timeline"],
        "learner_inputs": package["learner_inputs"],
        "outcome_releases": package["outcome_releases"],
        "evaluator_only_commitment": package["evaluator_only_commitment"],
    }


def _material_digest(package: Mapping[str, object]) -> str:
    return _digest_bytes(_canonical(_material_projection(package)))


def _v2_dataset_id(package: Mapping[str, object]) -> str:
    """Bind v2 identity to its envelope version, protocol, and staged material."""
    identity = {
        "package_version": package["package_version"],
        "protocol": package["protocol"],
        "material_sha256": _material_digest(package),
    }
    return "dataset-" + _digest_bytes(_canonical(identity))[:32]


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ContractError(f"cannot load JSON contract: {path}") from error
    if not isinstance(value, dict):
        raise ContractError(f"JSON contract must be an object: {path}")
    return value


def _exact(value: object, keys: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ContractError(f"{label} must contain exactly {sorted(keys)}")
    return value


def _text(value: object, label: str, *, maximum: int = 1000) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip() \
            or len(value) > maximum or not value.isprintable():
        raise ContractError(f"{label} must be bounded non-empty text")
    return value


def _opaque(value: object, label: str) -> str:
    value = _text(value, label, maximum=80)
    if OPAQUE.fullmatch(value) is None:
        raise ContractError(f"{label} must be an opaque identifier")
    return value


def _sha256(value: object, label: str) -> str:
    value = _text(value, label, maximum=64)
    if SHA256.fullmatch(value) is None:
        raise ContractError(f"{label} must be a lowercase SHA-256")
    return value


def _timestamp(value: object, label: str) -> datetime:
    raw = _text(value, label, maximum=64)
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as error:
        raise ContractError(f"{label} must be an ISO-8601 timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ContractError(f"{label} must include a UTC offset")
    return parsed.astimezone(UTC)


def _https(value: object, label: str) -> str:
    raw = _text(value, label, maximum=256)
    parsed = urlsplit(raw)
    if (
        parsed.scheme != "https"
        or SYNTHETIC_ORIGIN.fullmatch(parsed.netloc) is None
        or parsed.username
        or parsed.password
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ContractError(f"{label} must be an opaque credential-free synthetic HTTPS origin")
    return raw.rstrip("/")


def _rules_digest() -> str:
    return _digest_bytes(_canonical([asdict(rule) for rule in FNB_RULES]))


def verify_frozen_learner(protocol: dict[str, Any], repository: Path) -> dict[str, object]:
    learner = protocol.get("frozen_learner")
    if not isinstance(learner, dict):
        raise ContractError("protocol frozen_learner is missing")
    components = learner.get("component_sha256")
    if not isinstance(components, dict) or not components:
        raise ContractError("protocol component identities are missing")
    mismatches = []
    for relative, expected in sorted(components.items()):
        _sha256(expected, f"component hash {relative}")
        path = repository / relative
        actual = _digest_file(path) if path.is_file() else None
        if actual != expected:
            mismatches.append({"path": relative, "expected": expected, "actual": actual})
    runtime = {
        "semantic_evaluator_version": SEMANTIC_EVALUATOR_VERSION,
        "fnb_assessment_version": FNB_VERSION,
        "fnb_rules_sha256": _rules_digest(),
        "baseline_model": BASELINE_MODEL,
        "revised_model": REVISED_MODEL,
        "horizon_seconds": int(HORIZON.total_seconds()),
        "maximum_records_per_batch": MAX_OUTCOME_RECORDS,
    }
    expected_runtime = {
        "semantic_evaluator_version": learner.get("semantic_evaluator_version"),
        "fnb_assessment_version": learner.get("fnb_assessment_version"),
        "fnb_rules_sha256": learner.get("fnb_rules_sha256"),
        "baseline_model": learner.get("prediction", {}).get("baseline_model"),
        "revised_model": learner.get("revision", {}).get("model"),
        "horizon_seconds": learner.get("prediction", {}).get("horizon_seconds"),
        "maximum_records_per_batch": learner.get("normalization", {}).get(
            "maximum_records_per_batch"
        ),
    }
    if runtime != expected_runtime:
        raise ContractError("runtime constants differ from the frozen protocol")
    if mismatches:
        raise ContractError("frozen learner component mismatch: " + json.dumps(mismatches))
    return {
        "source_commit": learner["source_commit"],
        "source_tree": learner["source_tree"],
        "component_count": len(components),
        "component_hashes_match": True,
        **runtime,
    }


def _validate_scalar(value: object, label: str) -> None:
    if value is not None and type(value) not in (str, int, float, bool):
        raise ContractError(f"{label} contains a non-scalar value")
    if isinstance(value, float) and not (-float("inf") < value < float("inf")):
        raise ContractError(f"{label} contains a non-finite value")


def _validate_batch(
    batch: object,
    *,
    label: str,
    company: str,
    expected_resource: str | None = None,
    expected_date: date | None = None,
    latest_date: date | None = None,
) -> tuple[str, str]:
    value = _exact(batch, {
        "resource_id", "authorization_id", "provenance_source", "identity_field",
        "company_field", "date_field", "records",
    }, label)
    resource = _opaque(value["resource_id"], f"{label}.resource_id")
    if expected_resource is not None and resource != expected_resource:
        raise ContractError(f"{label} resource differs from its resource declaration")
    authorization = _opaque(value["authorization_id"], f"{label}.authorization_id")
    _opaque(value["provenance_source"], f"{label}.provenance_source")
    identity = _opaque(value["identity_field"], f"{label}.identity_field")
    company_field = _opaque(value["company_field"], f"{label}.company_field")
    date_field = _opaque(value["date_field"], f"{label}.date_field")
    if len({identity, company_field, date_field}) != 3:
        raise ContractError(f"{label} technical provenance fields must be distinct")
    records = value["records"]
    if not isinstance(records, list) or not 1 <= len(records) <= 25:
        raise ContractError(f"{label} must contain 1..25 records")
    fields: set[str] | None = None
    identities = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict) or not 3 <= len(record) <= 40:
            raise ContractError(f"{label}.records[{index}] must be a bounded flat object")
        if set(record) & FORBIDDEN_RECORD_KEYS:
            raise ContractError(f"{label} exposes an evaluator answer or precomputed total")
        for key, item in record.items():
            _opaque(key, f"{label}.records[{index}] field")
            _validate_scalar(item, f"{label}.records[{index}]")
        if fields is None:
            fields = set(record)
        elif set(record) != fields:
            raise ContractError(f"{label} records must have one exact field scope")
        if not {identity, company_field, date_field} <= set(record):
            raise ContractError(f"{label} record lacks technical provenance fields")
        record_id = _text(record[identity], f"{label} record identity", maximum=256)
        if record_id in identities:
            raise ContractError(f"{label} contains a duplicate source record")
        identities.add(record_id)
        if record[company_field] != company:
            raise ContractError(f"{label} record company is outside scope")
        try:
            occurred = date.fromisoformat(_text(record[date_field], f"{label} date", maximum=10))
        except ValueError as error:
            raise ContractError(f"{label} record date must be ISO-8601") from error
        if expected_date is not None and occurred != expected_date:
            raise ContractError(f"{label} record is outside its committed cohort date")
        if latest_date is not None and occurred > latest_date:
            raise ContractError(f"{label} record is later than the discovery evidence cutoff")
    return resource, authorization


def _validate_environment(
    value: object, *, label: str, company: str, evidence_cutoff: datetime,
    allow_technical_metadata: bool = False,
    canonical_source_references: bool = False,
) -> dict[str, object]:
    environment = _exact(
        value, {"source_id", "metadata_authorization_id", "resources", "instruments"}, label
    )
    source = _https(environment["source_id"], f"{label}.source_id")
    authorizations = {_opaque(
        environment["metadata_authorization_id"], f"{label}.metadata_authorization_id"
    )}
    resources = environment["resources"]
    if not isinstance(resources, list) or not 1 <= len(resources) <= 8:
        raise ContractError(f"{label} must contain 1..8 resources")
    resource_ids = set()
    topology = []
    for index, item in enumerate(resources):
        resource = _exact(item, {"id", "fields", "historical_batch"},
                          f"{label}.resources[{index}]")
        resource_id = _opaque(resource["id"], f"{label}.resources[{index}].id")
        if resource_id in resource_ids:
            raise ContractError(f"{label} contains duplicate resources")
        resource_ids.add(resource_id)
        fields = resource["fields"]
        if not isinstance(fields, list) or not 1 <= len(fields) <= 32:
            raise ContractError(f"{label} resource fields must contain 1..32 declarations")
        field_ids = set()
        kinds = {}
        for field_index, field in enumerate(fields):
            declaration = _exact(field, {"id", "kind"},
                                 f"{label}.resources[{index}].fields[{field_index}]")
            field_id = _opaque(declaration["id"], "opaque field identifier")
            if field_id in field_ids:
                raise ContractError(f"{label} resource contains duplicate fields")
            if declaration["kind"] not in {"Int", "Float", "Currency", "Percent", "Date", "Link"}:
                raise ContractError(f"{label} contains an unsupported field kind")
            field_ids.add(field_id)
            kinds[field_id] = declaration["kind"]
        batch_resource, authorization = _validate_batch(
            resource["historical_batch"], label=f"{label}.resources[{index}].historical_batch",
            company=company, expected_resource=resource_id,
            latest_date=evidence_cutoff.date(),
        )
        del batch_resource
        batch = resource["historical_batch"]
        if batch["date_field"] not in field_ids or kinds[batch["date_field"]] != "Date":
            raise ContractError(f"{label} historical date field lacks Date metadata evidence")
        record_fields = set(batch["records"][0])
        metadata_scope = record_fields if allow_technical_metadata else record_fields - {
            batch["identity_field"], batch["company_field"]
        }
        if not field_ids <= metadata_scope:
            raise ContractError(f"{label} historical records omit discovered fields")
        if authorization in authorizations:
            raise ContractError(f"{label} reuses an authorization identity")
        authorizations.add(authorization)
        topology.append(tuple(sorted(kinds.values())))
    discovered_kinds = [kind for item in topology for kind in item]
    if (
        sum(kind in {"Int", "Float", "Currency", "Percent"} for kind in discovered_kinds) < 2
        or discovered_kinds.count("Date") < 1
        or discovered_kinds.count("Link") < 2
    ):
        raise ContractError(f"{label} lacks multiple plausible structural interpretations")
    instruments = environment["instruments"]
    if not isinstance(instruments, list) or not 2 <= len(instruments) <= 8:
        raise ContractError(f"{label} must contain 2..8 independently reviewed instruments")
    instrument_sources = set()
    origin_domains = set()
    for index, item in enumerate(instruments):
        instrument = _exact(item, {
            "source_id", "resource_id", "provenance_source", "classes",
            "authorization_id", "records", "origins",
        }, f"{label}.instruments[{index}]")
        instrument_source = _https(instrument["source_id"], "instrument source")
        if instrument_source == source or instrument_source in instrument_sources:
            raise ContractError(f"{label} instrument sources must be distinct from each other and source")
        instrument_sources.add(instrument_source)
        _opaque(instrument["resource_id"], "instrument resource")
        _opaque(instrument["provenance_source"], "instrument provenance")
        authorization = _opaque(instrument["authorization_id"], "instrument authorization")
        if authorization in authorizations:
            raise ContractError(f"{label} reuses an authorization identity")
        authorizations.add(authorization)
        classes = instrument["classes"]
        allowed_classes = {"process", "aggregate", "temporal", "relationship", "organizational"}
        if not isinstance(classes, list) or not classes or len(classes) != len(set(classes)) \
                or not set(classes) <= allowed_classes:
            raise ContractError(f"{label} instrument classes are invalid")
        records = instrument["records"]
        origins = instrument["origins"]
        if not isinstance(records, list) or not 1 <= len(records) <= 25 \
                or not isinstance(origins, list) or len(origins) != len(records):
            raise ContractError(f"{label} instrument records and origins must be bounded and paired")
        origin_by_id = {}
        for origin in origins:
            origin = _exact(origin, {"record_id", "roots", "parents"}, "instrument origin")
            record_id = _text(origin["record_id"], "origin record_id", maximum=200)
            roots = origin["roots"]
            parents = origin["parents"]
            if not isinstance(roots, list) or not 1 <= len(roots) <= 8 \
                    or not isinstance(parents, list) or len(parents) > 8:
                raise ContractError("instrument origin lineage is unbounded")
            parsed_roots = []
            for root in roots:
                if not isinstance(root, list) or len(root) != 2:
                    raise ContractError("instrument roots must be collector/fact pairs")
                domain = _text(root[0], "collector domain", maximum=128)
                fact = _text(root[1], "original fact", maximum=128)
                parsed_roots.append((domain, fact))
                origin_domains.add(domain)
            if len(set(parsed_roots)) != len(parsed_roots):
                raise ContractError("instrument origin roots must be unique")
            if record_id in origin_by_id:
                raise ContractError("duplicate instrument origin record")
            origin_by_id[record_id] = origin
        record_ids = set()
        for record in records:
            if not isinstance(record, dict) or set(record) not in (
                    set(ANCHOR_FIELDS), set(ANCHOR_FIELDS + AGGREGATE_FIELDS)):
                raise ContractError("instrument record must use the canonical anchor contract")
            for item in record.values():
                _validate_scalar(item, "instrument record")
            record_id = _text(record["id"], "instrument record id", maximum=200)
            if record_id in record_ids or record_id not in origin_by_id:
                raise ContractError("instrument record origin is missing or duplicated")
            record_ids.add(record_id)
            subject_source = (
                _https(record["subject_source"], "instrument subject source")
                if canonical_source_references else record["subject_source"]
            )
            if record["partition"] != company or subject_source != source \
                    or record["subject_resource"] not in resource_ids \
                    or record["evidence_class"] not in classes:
                raise ContractError("instrument scope or subject is inconsistent")
            try:
                observed_on = date.fromisoformat(
                    _text(record["on"], "instrument observation date", maximum=10)
                )
            except ValueError as error:
                raise ContractError("instrument observation date must be ISO-8601") from error
            if observed_on > evidence_cutoff.date():
                raise ContractError("instrument observation is later than the discovery cutoff")
    if len(origin_domains) < 2:
        raise ContractError(f"{label} does not disclose two collector independence domains")
    return {
        "source_id": source,
        "resource_ids": resource_ids,
        "authorizations": authorizations,
        "instrument_sources": instrument_sources,
        "collector_domains": origin_domains,
        "topology": tuple(sorted(topology)),
    }


def _validate_release(
    value: object,
    *,
    case_id: str,
    expected_available_at: datetime,
    company: str,
    resources: set[str],
) -> dict[str, object]:
    release = _exact(value, {"case_id", "available_at", "source_id", "batches"}, case_id)
    if release["case_id"] != case_id:
        raise ContractError(f"release order requires {case_id}")
    available_at = _timestamp(release["available_at"], f"{case_id}.available_at")
    if available_at != expected_available_at:
        raise ContractError(f"{case_id} release must equal its frozen one-day horizon")
    source = _https(release["source_id"], f"{case_id}.source_id")
    batches = release["batches"]
    if not isinstance(batches, list) or not 1 <= len(batches) <= 2:
        raise ContractError(f"{case_id} must contain one flat batch or a header/detail pair")
    authorizations = set()
    for index, batch in enumerate(batches):
        resource, authorization = _validate_batch(
            batch, label=f"{case_id}.batches[{index}]", company=company,
            expected_date=available_at.date(),
        )
        if resource not in resources:
            raise ContractError(f"{case_id} uses an undiscovered resource")
        if authorization in authorizations:
            raise ContractError(f"{case_id} reuses an authorization identity")
        authorizations.add(authorization)
    return {"source_id": source, "authorizations": authorizations}


def _validate_staged_material(
    package: Mapping[str, object], *, protocol: Mapping[str, object],
    protocol_sha256: str, dataset_id: str, content_identity: str,
    cutoff: datetime, start: datetime, authorship_review: str,
    allow_technical_metadata: bool = False,
    canonical_source_references: bool = False,
) -> dict[str, object]:
    if cutoff > start:
        raise ContractError("discovery evidence cutoff must not follow evaluation clock start")
    learner = _exact(
        package["learner_inputs"], {"tenant_id", "company_id", "development", "evaluation"},
        "learner_inputs",
    )
    tenant = _opaque(learner["tenant_id"], "learner tenant")
    company = _opaque(learner["company_id"], "learner company")
    del tenant
    development = _validate_environment(
        learner["development"], label="development", company=company,
        evidence_cutoff=cutoff, allow_technical_metadata=allow_technical_metadata,
        canonical_source_references=canonical_source_references,
    )
    evaluation = _validate_environment(
        learner["evaluation"], label="evaluation", company=company,
        evidence_cutoff=cutoff, allow_technical_metadata=allow_technical_metadata,
        canonical_source_references=canonical_source_references,
    )
    if development["source_id"] == evaluation["source_id"] \
            or development["instrument_sources"] & evaluation["instrument_sources"]:
        raise ContractError("development and evaluation source domains must be distinct")
    if development["authorizations"] & evaluation["authorizations"]:
        raise ContractError("development and evaluation authorizations must be distinct")
    if development["topology"] == evaluation["topology"]:
        raise ContractError("evaluation metadata must have a materially different topology")
    releases = _exact(
        package["outcome_releases"], {"development", "evaluation"}, "outcome_releases"
    )
    if not isinstance(releases["development"], list) or len(releases["development"]) != 2 \
            or not isinstance(releases["evaluation"], list) \
            or len(releases["evaluation"]) != 2:
        raise ContractError("exactly two development and two evaluation releases are required")
    release_results = []
    cases = (
        ("development-1", releases["development"][0], development, start + timedelta(days=1)),
        ("development-2", releases["development"][1], development, start + timedelta(days=2)),
        ("evaluation-1", releases["evaluation"][0], evaluation, start + timedelta(days=3)),
        ("evaluation-2", releases["evaluation"][1], evaluation, start + timedelta(days=4)),
    )
    all_authorizations = set(development["authorizations"]) | set(evaluation["authorizations"])
    for case_id, release, environment, expected in cases:
        result = _validate_release(
            release, case_id=case_id, expected_available_at=expected, company=company,
            resources=environment["resource_ids"],
        )
        if all_authorizations & result["authorizations"]:
            raise ContractError("outcome release reuses discovery or semantic authorization")
        all_authorizations.update(result["authorizations"])
        release_results.append((case_id, result))
    development_sources = {item[1]["source_id"] for item in release_results[:2]}
    evaluation_sources = {item[1]["source_id"] for item in release_results[2:]}
    if len(development_sources) != 1 or len(evaluation_sources) != 1 \
            or development_sources & evaluation_sources:
        raise ContractError("development/evaluation releases require distinct fixed sources")
    prior_sources = {
        development["source_id"], evaluation["source_id"],
        *development["instrument_sources"], *evaluation["instrument_sources"],
    }
    if development_sources & prior_sources or evaluation_sources & prior_sources:
        raise ContractError("outcome sources must be distinct from discovery and instruments")
    commitment = _exact(package["evaluator_only_commitment"], {
        "sealed_expected_results_sha256", "held_by", "economic_value_criterion",
    }, "evaluator_only_commitment")
    _sha256(commitment["sealed_expected_results_sha256"], "sealed expected results")
    _text(commitment["held_by"], "evaluator-only commitment holder")
    criterion = commitment["economic_value_criterion"]
    if criterion is not None:
        criterion = _exact(
            criterion, {"metric", "threshold", "unit", "evidence_required"},
            "economic_value_criterion",
        )
        for key in ("metric", "unit", "evidence_required"):
            _text(criterion[key], f"economic_value_criterion.{key}")
        if type(criterion["threshold"]) not in (int, float):
            raise ContractError("economic value threshold must be numeric")
    return {
        "status": "READY_FOR_SINGLE_FROZEN_EVALUATION",
        "protocol_version": protocol["protocol_version"],
        "protocol_sha256": protocol_sha256,
        "dataset_id": dataset_id,
        "dataset_material_sha256": content_identity,
        "authorship_review": authorship_review,
        "release_cases": [case_id for case_id, _ in release_results],
        "economic_value_criterion_preregistered": criterion is not None,
        "execution_allowed": False,
        "allow_live_customer_access": False,
        "LIVE_PILOT_READY": False,
    }


def _validate_package_v1(
    package: dict[str, Any], *, protocol: dict[str, Any], protocol_sha256: str,
    require_independent: bool = True,
) -> dict[str, object]:
    package = _exact(package, {
        "package_version", "dataset_id", "protocol", "authorship", "timeline",
        "learner_inputs", "outcome_releases", "evaluator_only_commitment",
    }, "evaluation package")
    if package["package_version"] != PACKAGE_VERSION:
        raise ContractError("unsupported evaluation package version")
    protocol_ref = _exact(package["protocol"], {"version", "sha256"}, "protocol reference")
    if protocol_ref != {"version": PROTOCOL_VERSION, "sha256": protocol_sha256}:
        raise ContractError("dataset is not bound to this exact frozen protocol")
    dataset_id = _text(package["dataset_id"], "dataset_id", maximum=72)
    content_identity = _material_digest(package)
    if dataset_id != "dataset-" + content_identity[:32]:
        raise ContractError("dataset_id does not bind the frozen staged material")
    authorship = _exact(package["authorship"], {
        "prepared_by", "preparer_role", "prepared_at", "fixed_at", "independence_basis",
        "engine_source_seen_before_fixing", "engine_source_seen_details",
        "engine_results_seen_before_fixing", "engine_results_seen_details",
        "preparer_is_engine_implementer", "shared_fixture_engine_authorship",
        "answers_fixed_before_execution", "learner_visible_material",
        "evaluator_retained_material", "review",
    }, "authorship")
    for key in ("prepared_by", "preparer_role", "independence_basis",
                "engine_source_seen_details", "engine_results_seen_details"):
        _text(authorship[key], f"authorship.{key}")
    for key in ("engine_source_seen_before_fixing", "engine_results_seen_before_fixing"):
        if type(authorship[key]) is not bool:
            raise ContractError(f"authorship.{key} must be boolean")
    for key in ("preparer_is_engine_implementer", "shared_fixture_engine_authorship",
                "answers_fixed_before_execution"):
        if type(authorship[key]) is not bool:
            raise ContractError(f"authorship.{key} must be boolean")
    if authorship["answers_fixed_before_execution"] is not True:
        raise ContractError("answers must be fixed before execution")
    if require_independent and (
        authorship["preparer_is_engine_implementer"] is not False
        or authorship["shared_fixture_engine_authorship"] is not False
    ):
        raise ContractError("dataset does not establish independent precommitted authorship")
    if not require_independent and not (
        authorship["preparer_is_engine_implementer"] is True
        or authorship["shared_fixture_engine_authorship"] is True
    ):
        raise ContractError("infrastructure fixture must disclose shared implementation authorship")
    for key in ("learner_visible_material", "evaluator_retained_material"):
        values = authorship[key]
        if not isinstance(values, list) or not values or len(values) != len(set(values)):
            raise ContractError(f"authorship.{key} must be a non-empty unique list")
        for item in values:
            _text(item, f"authorship.{key} item")
    prepared_at = _timestamp(authorship["prepared_at"], "authorship.prepared_at")
    fixed_at = _timestamp(authorship["fixed_at"], "authorship.fixed_at")
    review = _exact(authorship["review"], {
        "status", "reviewed_by", "reviewer_role", "reviewed_at", "evidence_sha256",
    }, "authorship.review")
    if review["status"] not in {"VERIFIED", "NOT_PROVEN"}:
        raise ContractError("authorship review status is invalid")
    if require_independent and review["status"] != "VERIFIED":
        raise ContractError("independent authorship review is not declared verified")
    _text(review["reviewed_by"], "authorship.review.reviewed_by")
    _text(review["reviewer_role"], "authorship.review.reviewer_role")
    reviewed_at = _timestamp(review["reviewed_at"], "authorship.review.reviewed_at")
    _sha256(review["evidence_sha256"], "authorship.review.evidence_sha256")
    if review["reviewed_by"] == authorship["prepared_by"]:
        raise ContractError("authorship review must be performed by a different person")
    timeline = _exact(
        package["timeline"], {"discovery_evidence_cutoff", "evaluation_clock_start"}, "timeline"
    )
    cutoff = _timestamp(timeline["discovery_evidence_cutoff"], "discovery evidence cutoff")
    start = _timestamp(timeline["evaluation_clock_start"], "evaluation clock start")
    if not prepared_at <= cutoff <= fixed_at <= reviewed_at <= start:
        raise ContractError("authorship, review and evidence-cutoff chronology is invalid")
    return _validate_staged_material(
        package, protocol=protocol, protocol_sha256=protocol_sha256,
        dataset_id=dataset_id, content_identity=content_identity,
        cutoff=cutoff, start=start,
        authorship_review=(
            "DECLARED_PENDING_EVIDENCE" if require_independent else "NOT_PROVEN"
        ),
    )


def _validate_original_preparation(value: object) -> dict[str, object]:
    original = _exact(value, {"status", "timestamp", "reason"}, "original preparation")
    if original["status"] not in {"KNOWN", "UNKNOWN"}:
        raise ContractError("original preparation status must be KNOWN or UNKNOWN")
    reason = _text(original["reason"], "original preparation reason")
    if original["status"] == "UNKNOWN":
        if original["timestamp"] is not None:
            raise ContractError("UNKNOWN original preparation must not invent a timestamp")
        return {"status": "UNKNOWN", "timestamp": None, "reason": reason}
    timestamp = _timestamp(original["timestamp"], "original preparation timestamp")
    return {"status": "KNOWN", "timestamp": timestamp.isoformat(), "reason": reason}


def _validate_package_v2(
    package: dict[str, Any], *, protocol: dict[str, Any], protocol_sha256: str,
    require_independent: bool = True,
) -> dict[str, object]:
    package = _exact(package, {
        "package_version", "dataset_id", "protocol", "authorship", "timeline",
        "learner_inputs", "outcome_releases", "evaluator_only_commitment",
    }, "evaluation package")
    if package["package_version"] != PACKAGE_VERSION_V2:
        raise ContractError("unsupported evaluation package version")
    protocol_ref = _exact(package["protocol"], {"version", "sha256"}, "protocol reference")
    if protocol_ref != {"version": PROTOCOL_VERSION_V2, "sha256": protocol_sha256}:
        raise ContractError("dataset is not bound to this exact successor protocol")
    if protocol.get("protocol_version") != PROTOCOL_VERSION_V2:
        raise ContractError("mixed evaluation contract versions are forbidden")
    dataset_id = _text(package["dataset_id"], "dataset_id", maximum=72)
    expected_dataset_id = _v2_dataset_id(package)
    if dataset_id != expected_dataset_id:
        raise ContractError("dataset_id does not bind the v2 envelope and staged material")
    authorship = _exact(package["authorship"], {
        "prepared_by", "preparer_role", "independence_basis",
        "engine_source_seen_before_fixing", "engine_source_seen_details",
        "engine_results_seen_before_fixing", "engine_results_seen_details",
        "preparer_is_engine_implementer", "shared_fixture_engine_authorship",
        "answers_fixed_before_execution", "learner_visible_material",
        "evaluator_retained_material", "original_preparation",
        "exposure_disclosure_sha256", "generation_record_sha256", "legacy_source",
    }, "authorship")
    for key in (
        "prepared_by", "preparer_role", "independence_basis",
        "engine_source_seen_details", "engine_results_seen_details",
    ):
        _text(authorship[key], f"authorship.{key}")
    for key in (
        "engine_source_seen_before_fixing", "engine_results_seen_before_fixing",
        "preparer_is_engine_implementer", "shared_fixture_engine_authorship",
        "answers_fixed_before_execution",
    ):
        if type(authorship[key]) is not bool:
            raise ContractError(f"authorship.{key} must be boolean")
    if authorship["answers_fixed_before_execution"] is not True:
        raise ContractError("answers must be fixed before execution")
    if require_independent and (
        authorship["preparer_is_engine_implementer"] is not False
        or authorship["shared_fixture_engine_authorship"] is not False
    ):
        raise ContractError("dataset does not declare independent precommitted authorship")
    if not require_independent and not (
        authorship["preparer_is_engine_implementer"] is True
        or authorship["shared_fixture_engine_authorship"] is True
    ):
        raise ContractError("infrastructure fixture must disclose shared implementation authorship")
    for key in ("learner_visible_material", "evaluator_retained_material"):
        values = authorship[key]
        if not isinstance(values, list) or not values or len(values) != len(set(values)):
            raise ContractError(f"authorship.{key} must be a non-empty unique list")
        for item in values:
            _text(item, f"authorship.{key} item")
    original = _validate_original_preparation(authorship["original_preparation"])
    exposure_digest = _sha256(
        authorship["exposure_disclosure_sha256"], "authorship exposure disclosure"
    )
    generation_digest = _sha256(
        authorship["generation_record_sha256"], "authorship generation record"
    )
    legacy = authorship["legacy_source"]
    if legacy is not None:
        legacy = _exact(legacy, {
            "package_version", "package_sha256", "dataset_id", "authorship_sha256",
        }, "legacy source")
        if legacy["package_version"] != PACKAGE_VERSION:
            raise ContractError("legacy source must identify an original v1 package")
        _sha256(legacy["package_sha256"], "legacy package digest")
        _text(legacy["dataset_id"], "legacy dataset id", maximum=72)
        _sha256(legacy["authorship_sha256"], "legacy authorship digest")
    timeline = _exact(
        package["timeline"], {"discovery_evidence_cutoff", "evaluation_clock_start"},
        "timeline",
    )
    cutoff = _timestamp(timeline["discovery_evidence_cutoff"], "discovery evidence cutoff")
    start = _timestamp(timeline["evaluation_clock_start"], "evaluation clock start")
    validation = _validate_staged_material(
        package, protocol=protocol, protocol_sha256=protocol_sha256,
        dataset_id=dataset_id, content_identity=_material_digest(package),
        cutoff=cutoff, start=start, authorship_review="PENDING_SEPARATE_ENVELOPE",
        allow_technical_metadata=True,
        canonical_source_references=True,
    )
    validation.update({
        "original_preparation": original,
        "exposure_disclosure_sha256": exposure_digest,
        "generation_record_sha256": generation_digest,
        "legacy_source_retained": legacy is not None,
    })
    return validation


def validate_package(
    package: dict[str, Any], *, protocol: dict[str, Any], protocol_sha256: str,
    require_independent: bool = True,
) -> dict[str, object]:
    """Strictly dispatch a package to its matching versioned contract."""
    version = package.get("package_version")
    protocol_version = protocol.get("protocol_version")
    if version == PACKAGE_VERSION and protocol_version == PROTOCOL_VERSION:
        return _validate_package_v1(
            package, protocol=protocol, protocol_sha256=protocol_sha256,
            require_independent=require_independent,
        )
    if version == PACKAGE_VERSION_V2 and protocol_version == PROTOCOL_VERSION_V2:
        return _validate_package_v2(
            package, protocol=protocol, protocol_sha256=protocol_sha256,
            require_independent=require_independent,
        )
    if version in {PACKAGE_VERSION, PACKAGE_VERSION_V2} \
            or protocol_version in SUPPORTED_PROTOCOLS:
        raise ContractError("mixed evaluation contract versions are forbidden")
    raise ContractError("unsupported evaluation package or protocol version")


def verify_review_evidence(
    package: Mapping[str, object], validation: Mapping[str, object], path: Path,
) -> dict[str, object]:
    """Bind an available review record to the frozen package without authenticating people."""
    document = _exact(_load(path), {
        "review_version", "dataset_id", "protocol_sha256", "dataset_material_sha256",
        "prepared_by", "fixed_at", "reviewed_by", "reviewed_at", "review_scope",
    }, "review evidence")
    authorship = package["authorship"]
    if not isinstance(authorship, Mapping):
        raise ContractError("package authorship is missing")
    review = authorship["review"]
    if not isinstance(review, Mapping):
        raise ContractError("package review is missing")
    expected = {
        "review_version": REVIEW_VERSION,
        "dataset_id": validation["dataset_id"],
        "protocol_sha256": validation["protocol_sha256"],
        "dataset_material_sha256": validation["dataset_material_sha256"],
        "prepared_by": authorship["prepared_by"],
        "fixed_at": authorship["fixed_at"],
        "reviewed_by": review["reviewed_by"],
        "reviewed_at": review["reviewed_at"],
    }
    for key, value in expected.items():
        if document[key] != value:
            raise ContractError(f"review evidence {key} does not bind the package")
    _text(document["review_scope"], "review evidence scope")
    actual = _digest_file(path)
    if actual != review["evidence_sha256"]:
        raise ContractError("review evidence digest does not match the package")
    return {
        "status": "AVAILABLE_AND_PACKAGE_BOUND",
        "sha256": actual,
        "identity_authentication": "NOT_PROVEN_BY_DIGEST_ALONE",
    }


def create_freeze_receipt_v2(
    package: dict[str, Any], validation: Mapping[str, object], *,
    package_path: Path, exposure_disclosure: Path, generation_record: Path,
    observed_at: datetime | None = None,
) -> dict[str, object]:
    """Create a local receipt without opening evaluator-retained sealed answers."""
    if package.get("package_version") != PACKAGE_VERSION_V2:
        raise ContractError("freeze receipts are only defined for v2 packages")
    authorship = package["authorship"]
    if not isinstance(authorship, Mapping):
        raise ContractError("package authorship is missing")
    exposure_sha256 = _digest_file(exposure_disclosure)
    generation_sha256 = _digest_file(generation_record)
    if exposure_sha256 != authorship["exposure_disclosure_sha256"]:
        raise ContractError("exposure disclosure digest does not bind the package")
    if generation_sha256 != authorship["generation_record_sha256"]:
        raise ContractError("generation record digest does not bind the package")
    observed = (observed_at or datetime.now(UTC)).astimezone(UTC)
    return {
        "freeze_receipt_version": FREEZE_RECEIPT_VERSION_V2,
        "protocol": {
            "version": PROTOCOL_VERSION_V2,
            "sha256": validation["protocol_sha256"],
        },
        "package": {
            "version": PACKAGE_VERSION_V2,
            "sha256": _digest_file(package_path),
            "dataset_id": validation["dataset_id"],
            "material_sha256": validation["dataset_material_sha256"],
        },
        "sealed_expected_results_sha256": package["evaluator_only_commitment"][
            "sealed_expected_results_sha256"
        ],
        "exposure_disclosure_sha256": exposure_sha256,
        "generation_record_sha256": generation_sha256,
        "observed_at": observed.isoformat(),
        "clock_provenance": {
            "source": "LOCAL_CONTROLLER_CLOCK",
            "authenticated_time": False,
            "claim": "RECEIPT_OBSERVATION_ONLY",
        },
    }


def verify_freeze_receipt_v2(
    package: Mapping[str, object], validation: Mapping[str, object], *,
    receipt_path: Path, package_path: Path, exposure_disclosure: Path,
    generation_record: Path, not_after: datetime,
) -> dict[str, object]:
    receipt = _exact(_load(receipt_path), {
        "freeze_receipt_version", "protocol", "package",
        "sealed_expected_results_sha256", "exposure_disclosure_sha256",
        "generation_record_sha256", "observed_at", "clock_provenance",
    }, "freeze receipt")
    if receipt["freeze_receipt_version"] != FREEZE_RECEIPT_VERSION_V2:
        raise ContractError("unsupported freeze receipt version")
    protocol_ref = _exact(receipt["protocol"], {"version", "sha256"}, "receipt protocol")
    if protocol_ref != {
        "version": PROTOCOL_VERSION_V2,
        "sha256": validation["protocol_sha256"],
    }:
        raise ContractError("freeze receipt protocol does not bind the package")
    package_ref = _exact(
        receipt["package"], {"version", "sha256", "dataset_id", "material_sha256"},
        "receipt package",
    )
    expected_package = {
        "version": PACKAGE_VERSION_V2,
        "sha256": _digest_file(package_path),
        "dataset_id": validation["dataset_id"],
        "material_sha256": validation["dataset_material_sha256"],
    }
    if package_ref != expected_package:
        raise ContractError("freeze receipt does not bind the exact raw package and material")
    commitment = package["evaluator_only_commitment"]
    authorship = package["authorship"]
    if not isinstance(commitment, Mapping) or not isinstance(authorship, Mapping):
        raise ContractError("validated package envelope is missing")
    expected_hashes = {
        "sealed_expected_results_sha256": commitment["sealed_expected_results_sha256"],
        "exposure_disclosure_sha256": authorship["exposure_disclosure_sha256"],
        "generation_record_sha256": authorship["generation_record_sha256"],
    }
    actual_hashes = {
        "sealed_expected_results_sha256": receipt["sealed_expected_results_sha256"],
        "exposure_disclosure_sha256": _digest_file(exposure_disclosure),
        "generation_record_sha256": _digest_file(generation_record),
    }
    for key, expected in expected_hashes.items():
        if receipt[key] != expected or actual_hashes[key] != expected:
            raise ContractError(f"freeze receipt {key} does not bind retained evidence")
    provenance = _exact(
        receipt["clock_provenance"], {"source", "authenticated_time", "claim"},
        "receipt clock provenance",
    )
    if provenance != {
        "source": "LOCAL_CONTROLLER_CLOCK",
        "authenticated_time": False,
        "claim": "RECEIPT_OBSERVATION_ONLY",
    }:
        raise ContractError("freeze receipt must not claim authenticated time")
    observed = _timestamp(receipt["observed_at"], "freeze receipt observed_at")
    if observed > not_after.astimezone(UTC):
        raise ContractError("freeze receipt timestamp is in the future")
    original = authorship["original_preparation"]
    if isinstance(original, Mapping) and original.get("status") == "KNOWN":
        prepared = _timestamp(original.get("timestamp"), "original preparation timestamp")
        if prepared > observed:
            raise ContractError("known original preparation follows the freeze receipt")
    return {
        "status": "AVAILABLE_AND_PACKAGE_BOUND",
        "sha256": _digest_file(receipt_path),
        "observed_at": observed,
        "authenticated_time": False,
        "authorship_authentication": "NOT_PROVEN_BY_RECEIPT",
    }


def verify_review_evidence_v2(
    package: Mapping[str, object], validation: Mapping[str, object], *,
    receipt: Mapping[str, object], path: Path, run_start: datetime,
) -> dict[str, object]:
    document = _exact(_load(path), {
        "review_version", "protocol", "package", "freeze_receipt_sha256",
        "freeze_observed_at", "prepared_by", "preparer_role", "reviewed_by",
        "reviewer_role", "reviewed_at", "review_scope", "decision",
        "independence_basis", "identity_authentication",
    }, "review evidence")
    if document["review_version"] != REVIEW_VERSION_V2:
        raise ContractError("unsupported review evidence version")
    if document["protocol"] != {
        "version": PROTOCOL_VERSION_V2,
        "sha256": validation["protocol_sha256"],
    }:
        raise ContractError("review protocol does not bind the package")
    if document["package"] != {
        "version": PACKAGE_VERSION_V2,
        "dataset_id": validation["dataset_id"],
        "material_sha256": validation["dataset_material_sha256"],
    }:
        raise ContractError("review package identity does not bind the package")
    authorship = package["authorship"]
    if not isinstance(authorship, Mapping):
        raise ContractError("package authorship is missing")
    expected_values = {
        "freeze_receipt_sha256": receipt["sha256"],
        "freeze_observed_at": receipt["observed_at"].isoformat(),
        "prepared_by": authorship["prepared_by"],
        "preparer_role": authorship["preparer_role"],
    }
    for key, expected in expected_values.items():
        if document[key] != expected:
            raise ContractError(f"review evidence {key} does not bind retained evidence")
    reviewer = _text(document["reviewed_by"], "reviewed_by")
    _text(document["reviewer_role"], "reviewer_role")
    _text(document["review_scope"], "review_scope")
    _text(document["independence_basis"], "review independence basis")
    if reviewer == authorship["prepared_by"]:
        raise ContractError("review must be performed by a different declared person")
    if document["decision"] != "VERIFIED":
        raise ContractError("independent execution requires a VERIFIED review decision")
    if document["identity_authentication"] != "NOT_PROVEN_BY_DIGEST_ALONE":
        raise ContractError("review must not overstate identity authentication")
    reviewed_at = _timestamp(document["reviewed_at"], "reviewed_at")
    freeze_at = receipt["observed_at"]
    if not isinstance(freeze_at, datetime):
        raise ContractError("verified freeze receipt is required")
    if not freeze_at <= reviewed_at <= run_start.astimezone(UTC):
        raise ContractError("freeze, review and controller run chronology is invalid")
    return {
        "status": "AVAILABLE_AND_PACKAGE_BOUND",
        "sha256": _digest_file(path),
        "reviewed_at": reviewed_at.isoformat(),
        "identity_authentication": "NOT_PROVEN_BY_DIGEST_ALONE",
        "independence_authentication": "REQUIRES_HUMAN_OR_EXTERNAL_TRUST",
    }


def _bind_trusted_review_approval(
    review_result: Mapping[str, object], supplied_sha256: object,
) -> dict[str, str]:
    """Bind a separately trusted controller approval to the exact review bytes."""
    supplied = _sha256(supplied_sha256, "trusted review approval digest")
    if supplied != review_result.get("sha256"):
        raise ContractError("trusted review approval does not bind the review artifact")
    return {
        "status": "SEPARATELY_TRUSTED_EXACT_REVIEW_DIGEST",
        "review_evidence_sha256": supplied,
    }


def convert_v1_package(
    package: dict[str, Any], *, original_package_sha256: str,
    protocol_sha256_v1: str, protocol_sha256_v2: str,
    exposure_disclosure_sha256: str, generation_record_sha256: str,
    unknown_preparation_reason: str,
) -> tuple[dict[str, object], dict[str, object]]:
    """Create a successor envelope while retaining the original v1 artifact by digest."""
    if package.get("package_version") != PACKAGE_VERSION:
        raise ContractError("conversion requires an original v1 package")
    if package.get("protocol") != {
        "version": PROTOCOL_VERSION, "sha256": protocol_sha256_v1,
    }:
        raise ContractError("original package is not bound to the supplied v1 protocol")
    _sha256(original_package_sha256, "original package digest")
    _sha256(exposure_disclosure_sha256, "exposure disclosure digest")
    _sha256(generation_record_sha256, "generation record digest")
    reason = _text(unknown_preparation_reason, "unknown preparation reason")
    original_authorship = package["authorship"]
    if not isinstance(original_authorship, dict):
        raise ContractError("original package authorship is missing")
    converted = json.loads(json.dumps(package))
    converted["package_version"] = PACKAGE_VERSION_V2
    converted["protocol"] = {
        "version": PROTOCOL_VERSION_V2, "sha256": protocol_sha256_v2,
    }
    converted["authorship"] = {
        key: original_authorship[key] for key in (
            "prepared_by", "preparer_role", "independence_basis",
            "engine_source_seen_before_fixing", "engine_source_seen_details",
            "engine_results_seen_before_fixing", "engine_results_seen_details",
            "preparer_is_engine_implementer", "shared_fixture_engine_authorship",
            "answers_fixed_before_execution", "learner_visible_material",
            "evaluator_retained_material",
        )
    }
    converted["authorship"].update({
        "original_preparation": {
            "status": "UNKNOWN", "timestamp": None, "reason": reason,
        },
        "exposure_disclosure_sha256": exposure_disclosure_sha256,
        "generation_record_sha256": generation_record_sha256,
        "legacy_source": {
            "package_version": PACKAGE_VERSION,
            "package_sha256": original_package_sha256,
            "dataset_id": package["dataset_id"],
            "authorship_sha256": _digest_bytes(_canonical(original_authorship)),
        },
    })
    converted["dataset_id"] = _v2_dataset_id(converted)
    old_material = _material_digest(package)
    new_material = _material_digest(converted)
    if old_material != new_material:
        raise ContractError("conversion changed immutable staged material")
    old_commitment = package["evaluator_only_commitment"]["sealed_expected_results_sha256"]
    new_commitment = converted["evaluator_only_commitment"]["sealed_expected_results_sha256"]
    if old_commitment != new_commitment:
        raise ContractError("conversion changed the sealed oracle commitment")
    report = {
        "conversion_version": "orion-v1-to-v2-envelope-conversion-v1",
        "original": {
            "package_version": PACKAGE_VERSION,
            "package_sha256": original_package_sha256,
            "dataset_id": package["dataset_id"],
            "protocol_sha256": protocol_sha256_v1,
            "authorship_sha256": _digest_bytes(_canonical(original_authorship)),
        },
        "successor": {
            "package_version": PACKAGE_VERSION_V2,
            "dataset_id": converted["dataset_id"],
            "protocol_sha256": protocol_sha256_v2,
            "authorship_sha256": _digest_bytes(_canonical(converted["authorship"])),
        },
        "material_sha256_before": old_material,
        "material_sha256_after": new_material,
        "operational_material_identical": True,
        "sealed_expected_results_sha256": old_commitment,
        "original_artifact_overwritten": False,
        "original_preparation_status": "UNKNOWN",
        "original_preparation_reason": reason,
    }
    return converted, report


class _PackageMetadataAdapter:
    """Translate opaque structural declarations through canonical metadata admission."""

    def __init__(self, environment: Mapping[str, object], calls: list[dict[str, str]]):
        self.source_id = str(environment["source_id"])
        self._resources = {
            str(resource["id"]): resource for resource in environment["resources"]
        }
        self._calls = calls

    def catalog(self, permit: object, requested: int) -> tuple[tuple[str, ...], bool]:
        permit.claim_io(self.source_id)
        self._calls.append({"kind": "metadata_catalog", "source": self.source_id})
        catalog = tuple(sorted(self._resources))
        return catalog[:requested], len(catalog) < requested

    def schema(self, permit: object, resource: str) -> ScopeProposal:
        permit.claim_io(self.source_id)
        self._calls.append({"kind": "metadata_schema", "source": self.source_id})
        declaration = self._resources[resource]
        kinds = {
            "Int": "number", "Float": "number", "Currency": "number",
            "Percent": "number", "Date": "date", "Link": "reference",
        }
        fields = tuple(FieldDeclaration(
            resource, str(field["id"]), kinds[str(field["kind"])], str(field["kind"])
        ) for field in declaration["fields"])
        dates = tuple(field.name for field in fields if field.kind == "date")
        return ScopeProposal(
            resource, tuple(field.name for field in fields), dates,
            interpret_schema(resource, fields),
        )


class _PackageRecordReader:
    def __init__(
        self, source_id: str, provenance_source: str,
        resource: str, records: tuple[Mapping[str, object], ...],
        clock: Callable[[], datetime], calls: list[dict[str, str]],
    ) -> None:
        self.source_id = source_id
        self._provenance_source = provenance_source
        self._resource = resource
        self._records = records
        self._clock = clock
        self._calls = calls

    def read(self, permit: object) -> tuple[Observation, ...]:
        request, _ = permit.claim_io(self.source_id)
        if request.resource != self._resource:
            raise ValueError("package reader resource mismatch")
        self._calls.append({"kind": "record_batch", "source": self.source_id})
        return tuple(Observation(Evidence(
            EvidenceKind.EXPERIMENT, self._provenance_source,
            {"resource": self._resource, "record": dict(record)},
            observed_at=self._clock(), tenant_id=request.tenant_id,
        )) for record in self._records)


def _record_dates(batch: Mapping[str, object]) -> tuple[date, date]:
    field = str(batch["date_field"])
    values = tuple(date.fromisoformat(str(record[field])) for record in batch["records"])
    return min(values), max(values)


def _admit_batch(
    batch: Mapping[str, object], *, tenant: str, company: str, source: str,
    clock: Callable[[], datetime], authorized_ids: frozenset[str],
    calls: list[dict[str, str]],
) -> tuple[tuple[Observation, ...], PilotRequest]:
    records = tuple(dict(record) for record in batch["records"])
    fields = tuple(records[0])
    start, end = _record_dates(batch)
    request = PilotRequest(
        tenant, company, source, str(batch["resource_id"]), fields,
        str(batch["date_field"]), start, end, len(records),
    )
    grant = PilotAuthorization(
        str(batch["authorization_id"]), source,
        ReviewedReadWindow(
            tenant, company, str(batch["resource_id"]), fields,
            str(batch["date_field"]), start, end, clock() + timedelta(hours=1),
        ),
        str(batch["identity_field"]), str(batch["company_field"]),
        str(batch["provenance_source"]), EvidenceKind.EXPERIMENT, len(records),
    )
    observations = launch_pilot_read(
        request, authorization_id=grant.authorization_id,
        lookup=lambda identity: (
            grant if identity == grant.authorization_id and identity in authorized_ids else None
        ),
        adapter=_PackageRecordReader(
            source, grant.provenance_source, request.resource, records, clock, calls,
        ),
        clock=clock,
    )
    return observations, request


def package_authorization_references(package: Mapping[str, object]) -> frozenset[str]:
    """Collect references for the trusted controller; this does not issue grants."""
    learner = package["learner_inputs"]
    releases = package["outcome_releases"]
    if not isinstance(learner, Mapping) or not isinstance(releases, Mapping):
        raise ContractError("validated package structure required")
    identities = set()
    for phase in ("development", "evaluation"):
        environment = learner[phase]
        identities.add(str(environment["metadata_authorization_id"]))
        identities.update(
            str(resource["historical_batch"]["authorization_id"])
            for resource in environment["resources"]
        )
        identities.update(
            str(instrument["authorization_id"])
            for instrument in environment["instruments"]
        )
        identities.update(
            str(batch["authorization_id"])
            for release in releases[phase] for batch in release["batches"]
        )
    return frozenset(identities)


def _instrument_batch(instrument: Mapping[str, object]) -> dict[str, object]:
    records = tuple(dict(record) for record in instrument["records"])
    return {
        "resource_id": instrument["resource_id"],
        "authorization_id": instrument["authorization_id"],
        "provenance_source": instrument["provenance_source"],
        "identity_field": "id", "company_field": "partition", "date_field": "on",
        "records": records,
    }


def _discover_environment(
    environment: Mapping[str, object], *, tenant: str, company: str,
    evidence_time: datetime, authorized_ids: frozenset[str],
    calls: list[dict[str, str]],
) -> dict[str, object]:
    clock = lambda: evidence_time
    source = str(environment["source_id"])
    metadata_request = MetadataRequest(tenant, company, source)
    metadata_id = str(environment["metadata_authorization_id"])
    metadata_grant = MetadataAuthorization(
        metadata_id, metadata_request, evidence_time + timedelta(hours=1), True,
        len(environment["resources"]), len(environment["resources"]), (),
    )
    discovered = launch_pilot_metadata(
        metadata_request, authorization_id=metadata_id,
        lookup=lambda identity: (
            metadata_grant
            if identity == metadata_id and identity in authorized_ids else None
        ),
        adapter=_PackageMetadataAdapter(environment, calls), clock=clock,
    )
    archive: dict[object, object] = {
        observation.evidence.evidence_id: observation
        for observation in discovered.observations
    }
    base = RoleStudy(discovered.observations[0], evidence_lookup=archive.get)
    for resource in environment["resources"]:
        observations, request = _admit_batch(
            resource["historical_batch"], tenant=tenant, company=company,
            source=source, clock=clock, authorized_ids=authorized_ids, calls=calls,
        )
        for observation in observations:
            archive[observation.evidence.evidence_id] = observation
            archive[("scope", observation.evidence.evidence_id)] = request
        base.observe(observations, request=request)
    instruments = tuple(Instrument(
        str(item["source_id"]), str(item["resource_id"]),
        str(item["provenance_source"]), tuple(map(str, item["classes"])),
    ) for item in environment["instruments"])
    study = SemanticStudy(
        base, instruments=instruments, evidence_lookup=archive.get, rules=FNB_RULES,
    )
    for item in environment["instruments"]:
        origins = {str(origin["record_id"]): origin for origin in item["origins"]}
        grouped: dict[tuple[str, ...], list[Mapping[str, object]]] = {}
        for record in item["records"]:
            grouped.setdefault(tuple(record), []).append(record)
        for records in grouped.values():
            batch = _instrument_batch({**item, "records": records})
            observations, request = _admit_batch(
                batch, tenant=tenant, company=company, source=str(item["source_id"]),
                clock=clock, authorized_ids=authorized_ids, calls=calls,
            )
            for observation in observations:
                archive[observation.evidence.evidence_id] = observation
                archive[("scope", observation.evidence.evidence_id)] = request
                record_id = str(
                    observation.evidence.payload["provenance"]["source_record_id"]
                )
                origin = origins[record_id]
                archive[("origin", observation.evidence.evidence_id)] = Origin(
                    tuple(tuple(map(str, root)) for root in origin["roots"]),
                    tuple(UUID(str(parent)) for parent in origin["parents"]),
                )
            study.observe(observations)
    return assess_restaurant(study, tenant_id=tenant, company=company, source_id=source)


def _cohort_document(cohort: BusinessCohort) -> dict[str, object]:
    return {
        "entity_id": cohort.entity_id, "location_id": cohort.location_id,
        "window_start": cohort.window_start.isoformat(),
        "window_end": cohort.window_end.isoformat(),
    }


def _pending_commitments(
    ledger_path: Path, *, tenant: str, target: str, unit: str,
    cohort: BusinessCohort, models: tuple[str, ...],
) -> tuple[str, ...]:
    expected_cohort = _cohort_document(cohort)
    with sqlite3.connect(ledger_path) as database:
        rows = database.execute("""
            SELECT p.payload, o.identity FROM predictions p
            LEFT JOIN prediction_outcomes o
              ON p.tenant=o.tenant AND p.identity=o.identity
            WHERE p.tenant=? ORDER BY p.identity
        """, (tenant,)).fetchall()
    matches = []
    for payload, outcome_id in rows:
        prediction = json.loads(payload)
        if (
            outcome_id is None
            and prediction["target_definition"] == target
            and prediction["unit"] == unit
            and prediction["cohort"] == expected_cohort
            and prediction["model_version"] in models
            and prediction["horizon_end"] == cohort.window_end.isoformat()
        ):
            matches.append((prediction["model_version"], prediction["prediction_id"]))
    if tuple(sorted(model for model, _ in matches)) != tuple(sorted(models)):
        raise ContractError("matching durable pending prediction commitment is required")
    return tuple(identity for _, identity in sorted(matches))


class _StagedReleaseController:
    """Evaluator-side release gate; it holds records and grants outside learner state."""

    def __init__(
        self, *, phase: str, releases: tuple[Mapping[str, object], ...],
        ledger_path: Path, tenant: str, target: str, unit: str,
        company: str, state: dict[str, datetime], authorized_ids: frozenset[str],
        calls: list[dict[str, str]],
    ) -> None:
        self.phase = phase
        self.releases = releases
        self.ledger_path = ledger_path
        self.tenant = tenant
        self.target = target
        self.unit = unit
        self.company = company
        self.state = state
        self.authorized_ids = authorized_ids
        self.calls = calls
        self.index = 0
        self.checks: list[dict[str, object]] = []

    def acquire(self, queries: tuple[object, ...]) -> tuple[Observation, ...]:
        before = len(self.calls)
        if len(queries) != 1 or self.index >= len(self.releases):
            raise ContractError("one ordered staged outcome query is required")
        query = queries[0]
        release = self.releases[self.index]
        expected_case = f"{self.phase}-{self.index + 1}"
        if query.case_id != expected_case or release["case_id"] != expected_case:
            raise ContractError("outcome release does not match the requested case")
        available = _timestamp(release["available_at"], f"{expected_case}.available_at")
        if available != query.horizon_end or query.target_definition != self.target \
                or query.unit != self.unit:
            raise ContractError("outcome release does not match target, unit or horizon")
        models = ((BASELINE_MODEL,) if self.phase == "development"
                  else (BASELINE_MODEL, REVISED_MODEL))
        prediction_ids = _pending_commitments(
            self.ledger_path, tenant=self.tenant, target=self.target, unit=self.unit,
            cohort=query.cohort, models=models,
        )
        if self.state["now"] >= available:
            raise ContractError("outcome release is not prospective")
        self.state["now"] = available
        observations = []
        for batch in release["batches"]:
            admitted, _ = _admit_batch(
                batch, tenant=self.tenant, company=self.company,
                source=str(release["source_id"]), clock=lambda: self.state["now"],
                authorized_ids=self.authorized_ids, calls=self.calls,
            )
            observations.extend(admitted)
        self.checks.append({
            "case_id": expected_case, "prediction_ids": prediction_ids,
            "prediction_committed_before_release": True,
            "source_io_count": len(self.calls) - before,
        })
        self.index += 1
        return tuple(observations)


def _prediction_documents(ledger_path: Path) -> tuple[dict[str, object], ...]:
    with sqlite3.connect(ledger_path) as database:
        rows = database.execute(
            "SELECT payload FROM predictions ORDER BY recorded_at, identity"
        ).fetchall()
    return tuple(json.loads(payload) for (payload,) in rows)


def _unknown_execution_result(
    *, validation: Mapping[str, object], frozen_before: Mapping[str, object],
    frozen_after: Mapping[str, object], started: Mapping[str, object],
    independent: bool, calls: list[dict[str, str]], protocol_version: str,
) -> dict[str, object]:
    return {
        "version": protocol_version, "status": "UNKNOWN",
        "protocol_sha256": validation["protocol_sha256"],
        "dataset_identity": validation["dataset_id"],
        "frozen_learner_before": frozen_before,
        "frozen_learner_after": frozen_after,
        "learning_cycle": dict(started),
        "WHAT_ORION_DISCOVERED": "No uniquely supported operational question.",
        "WHAT_USEFUL_RELATIONSHIP_WAS_SUPPORTED": None,
        "WHAT_WAS_PREDICTED_AND_WHEN": (), "WHAT_ACTUALLY_HAPPENED": (),
        "LEARNING_LOOP_INTEGRITY": "BLOCKED",
        "INDEPENDENT_EVALUATION": "PASS" if independent else "BLOCKED",
        "SEMANTIC_UNDERSTANDING": "UNKNOWN",
        "PREDICTIVE_IMPROVEMENT": "INSUFFICIENT_EVIDENCE",
        "ECONOMIC_VALUE": "NOT_PROVEN",
        "REAL_ORGANIZATION_GENERALIZATION": "NOT_PROVEN",
        "source_io_count": len(calls), "outcome_source_io_count": 0,
        "execution_allowed": False, "allow_live_customer_access": False,
        "LIVE_PILOT_READY": False, "candidate_host_qualification": "UNRESOLVED",
    }


def execute_package(
    package: dict[str, Any], *, protocol: dict[str, Any], protocol_sha256: str,
    repository: Path, state_dir: Path, independent: bool,
    authorized_ids: frozenset[str] | None,
    trusted_review_approval_sha256: str | None,
    review_evidence: Path | None = None,
    package_path: Path | None = None,
    freeze_receipt: Path | None = None,
    exposure_disclosure: Path | None = None,
    generation_record: Path | None = None,
    controller_clock: Callable[[], datetime] | None = None,
) -> dict[str, object]:
    """Run one package through the frozen learner using trusted local separation."""
    frozen_before = verify_frozen_learner(protocol, repository)
    validation = validate_package(
        package, protocol=protocol, protocol_sha256=protocol_sha256,
        require_independent=independent,
    )
    protocol_version = str(protocol["protocol_version"])
    if independent and protocol_version == PROTOCOL_VERSION:
        raise ContractError(
            "v1 is validation/conversion-only; independent execution requires "
            "an explicitly converted v2 package and trusted review approval"
        )
    review_result = {"status": "NOT_PROVEN"}
    run_start: datetime | None = None
    receipt_result: dict[str, object] | None = None
    if independent:
        if review_evidence is None:
            raise ContractError("independent execution requires available review evidence")
        required = {
            "package_path": package_path,
            "freeze_receipt": freeze_receipt,
            "exposure_disclosure": exposure_disclosure,
            "generation_record": generation_record,
        }
        missing = [key for key, value in required.items() if value is None]
        if missing:
            raise ContractError(
                "v2 independent execution requires " + ", ".join(sorted(missing))
            )
        candidate_start = (controller_clock or (lambda: datetime.now(UTC)))()
        if candidate_start.tzinfo is None or candidate_start.utcoffset() is None:
            raise ContractError("controller clock must return an aware timestamp")
        candidate_start = candidate_start.astimezone(UTC)
        receipt_result = verify_freeze_receipt_v2(
            package, validation, receipt_path=freeze_receipt,
            package_path=package_path, exposure_disclosure=exposure_disclosure,
            generation_record=generation_record, not_after=candidate_start,
        )
        review_result = verify_review_evidence_v2(
            package, validation, receipt=receipt_result, path=review_evidence,
            run_start=candidate_start,
        )
        if trusted_review_approval_sha256 is None:
            raise ContractError(
                "v2 independent execution requires separately trusted review approval"
            )
        review_result["execution_approval"] = _bind_trusted_review_approval(
            review_result, trusted_review_approval_sha256
        )
        run_start = candidate_start
    elif review_evidence is not None or trusted_review_approval_sha256 is not None:
        raise ContractError(
            "infrastructure exercise cannot claim review evidence or trusted approval"
        )
    references = package_authorization_references(package)
    if authorized_ids is None:
        raise ContractError("execution requires separately trusted authorization IDs")
    authorized = frozenset(authorized_ids)
    if authorized != references:
        raise ContractError(
            "trusted controller authorization IDs must exactly match package references"
        )
    if state_dir.exists() and any(state_dir.iterdir()):
        raise ContractError("evaluation state directory must be new and empty")
    state_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = state_dir / "predictions.sqlite3"
    queue_path = state_dir / "events.sqlite3"
    if ledger_path.exists() or queue_path.exists():
        raise ContractError("evaluation state directory must not contain prior canonical state")
    controller_record_path: Path | None = None
    if run_start is not None:
        if receipt_result is None:
            raise ContractError("verified freeze receipt is required before run start")
        controller_record_path = state_dir / "controller-run.json"
        controller_record = {
            "record_version": "orion-evaluation-controller-run-v2",
            "protocol_sha256": protocol_sha256,
            "dataset_id": validation["dataset_id"],
            "dataset_material_sha256": validation["dataset_material_sha256"],
            "freeze_receipt_sha256": receipt_result["sha256"],
            "review_evidence_sha256": review_result["sha256"],
            "trusted_review_approval": review_result["execution_approval"],
            "run_started_at": run_start.isoformat(),
            "clock_authentication": "NOT_PROVEN_BY_LOCAL_CLOCK",
        }
        controller_record_path.write_text(
            json.dumps(controller_record, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
    learner = package["learner_inputs"]
    tenant, company = str(learner["tenant_id"]), str(learner["company_id"])
    cutoff = _timestamp(package["timeline"]["discovery_evidence_cutoff"], "cutoff")
    start = _timestamp(package["timeline"]["evaluation_clock_start"], "start")
    calls: list[dict[str, str]] = []
    development = _discover_environment(
        learner["development"], tenant=tenant, company=company, evidence_time=cutoff,
        authorized_ids=authorized, calls=calls,
    )
    evaluation = _discover_environment(
        learner["evaluation"], tenant=tenant, company=company, evidence_time=cutoff,
        authorized_ids=authorized, calls=calls,
    )
    state = {"now": start}
    started = begin_learning_cycle(
        development, evaluation, ledger_path=ledger_path, clock=lambda: state["now"],
    )
    if started["status"] == "UNKNOWN":
        frozen_after = verify_frozen_learner(protocol, repository)
        return _unknown_execution_result(
            validation=validation, frozen_before=frozen_before,
            frozen_after=frozen_after, started=started,
            independent=independent, calls=calls, protocol_version=protocol_version,
        )
    question = select_prediction_question(development)
    if question.status != "SUPPORTED" or question.target_definition is None \
            or question.unit is None:
        raise ContractError("committed cycle lacks its supported frozen question")
    outcome_call_start = len(calls)
    releases = package["outcome_releases"]
    controllers = {
        phase: _StagedReleaseController(
            phase=phase, releases=tuple(releases[phase]), ledger_path=ledger_path,
            tenant=tenant, target=question.target_definition, unit=question.unit,
            company=company, state=state, authorized_ids=authorized, calls=calls,
        ) for phase in ("development", "evaluation")
    }
    measured = resume_learning_cycle(
        ledger_path=ledger_path, queue_path=queue_path,
        tenant_id=str(started["tenant_id"]), cycle_id=str(started["cycle_id"]),
        acquire_development=controllers["development"].acquire,
        acquire_evaluation=controllers["evaluation"].acquire,
        clock=lambda: state["now"],
    )
    frozen_after = verify_frozen_learner(protocol, repository)
    if frozen_after != frozen_before:
        raise ContractError("frozen learner identity changed during evaluation")
    commitment = package["evaluator_only_commitment"]
    state_bytes = ledger_path.read_bytes() + queue_path.read_bytes()
    forbidden = (
        str(commitment["sealed_expected_results_sha256"]), str(commitment["held_by"])
    )
    if any(value.encode() in state_bytes for value in forbidden):
        raise ContractError("evaluator-only commitment entered learner state")
    predictions = _prediction_documents(ledger_path)
    improved = bool(measured["predictive_improvement"])
    result = {
        "version": protocol_version,
        "status": "COMPLETED" if independent else "INFRASTRUCTURE_VERIFIED",
        "protocol_sha256": protocol_sha256,
        "dataset_identity": validation["dataset_id"],
        "dataset_material_sha256": validation["dataset_material_sha256"],
        "evaluation_mode": "INDEPENDENT" if independent else "SELF_AUTHORED_INFRASTRUCTURE",
        "authorship_review": review_result,
        "frozen_learner_before": frozen_before, "frozen_learner_after": frozen_after,
        "WHAT_ORION_DISCOVERED": {
            "development_assessment_id": measured["assessment_id"],
            "evaluation_assessment_id": measured["evaluation_assessment_id"],
        },
        "WHAT_USEFUL_RELATIONSHIP_WAS_SUPPORTED": measured["relationship"],
        "WHY_THE_QUESTION_WAS_SELECTED": measured["question"],
        "WHAT_WAS_PREDICTED_AND_WHEN": predictions,
        "WHAT_ACTUALLY_HAPPENED": {
            "development": measured["development"]["operational_outcomes"],
            "evaluation": measured["evaluation"]["operational_outcomes"],
        },
        "HOW_THE_BASELINE_PERFORMED": {
            "development_brier": measured["development"]["brier"],
            "evaluation_brier": measured["evaluation"]["prior_brier"],
        },
        "WHAT_ORION_REVISED_AND_WHY": measured["revision"],
        "WHETHER_THE_REVISION_HELPED_LATER": improved,
        "WHAT_REMAINED_UNKNOWN": measured["remaining_unknowns"],
        "WHAT_EVIDENCE_WOULD_HELP_NEXT": (
            "More independently authored, complete later cohorts with retained review evidence.",
        ),
        "WHAT_AUTHORIZATION_THAT_WOULD_REQUIRE": (
            "New bounded synthetic read grants through the existing admission contracts.",
        ),
        "WHAT_ORION_WAS_NOT_PERMITTED_TO_DO": (
            "Access customer systems, read unreleased outcomes, write business data, or execute actions.",
        ),
        "learning_cycle": measured,
        "release_checks": tuple(
            check for phase in ("development", "evaluation")
            for check in controllers[phase].checks
        ),
        "source_io_count": len(calls),
        "outcome_source_io_count": len(calls) - outcome_call_start,
        "evaluator_only_material_in_learner_state": False,
        "LEARNING_LOOP_INTEGRITY": "PASS",
        "INDEPENDENT_EVALUATION": "PASS" if independent else "BLOCKED",
        "SEMANTIC_UNDERSTANDING": "SUPPORTED_WITHIN_TESTED_SCOPE",
        "PREDICTIVE_IMPROVEMENT": (
            "DEMONSTRATED_IN_THIS_EVALUATION" if improved else "NOT_DEMONSTRATED"
        ),
        "ECONOMIC_VALUE": "NOT_PROVEN",
        "REAL_ORGANIZATION_GENERALIZATION": "NOT_PROVEN",
        "execution_allowed": False, "allow_live_customer_access": False,
        "LIVE_PILOT_READY": False, "candidate_host_qualification": "UNRESOLVED",
    }
    if protocol_version == PROTOCOL_VERSION_V2:
        result["controller_run_start"] = (
            run_start.isoformat() if run_start is not None else None
        )
        result["controller_run_record_sha256"] = (
            _digest_file(controller_record_path)
            if controller_record_path is not None else None
        )
    return result


def execution_owner_report(result: Mapping[str, object]) -> str:
    return "\n".join((
        f"Evaluation status: {result['status']}",
        f"Dataset: {result['dataset_identity']}",
        f"Learning loop integrity: {result['LEARNING_LOOP_INTEGRITY']}",
        f"Independent evaluation: {result['INDEPENDENT_EVALUATION']}",
        f"Semantic understanding: {result['SEMANTIC_UNDERSTANDING']}",
        f"Predictive improvement: {result['PREDICTIVE_IMPROVEMENT']}",
        f"Economic value: {result['ECONOMIC_VALUE']}",
        "Real-organization generalization: NOT_PROVEN",
        "Trusted local evaluator separation only; no hostile-process containment claimed.",
        "Candidate-host qualification remains unresolved.",
        "execution_allowed=false; allow_live_customer_access=false; LIVE_PILOT_READY=false",
    )) + "\n"


def blocked_result(protocol: dict[str, Any], protocol_sha256: str) -> dict[str, object]:
    learner = protocol["frozen_learner"]
    return {
        "version": protocol["protocol_version"],
        "status": "BLOCKED",
        "protocol_sha256": protocol_sha256,
        "dataset_identity": None,
        "frozen_learner": {
            "source_commit": learner["source_commit"],
            "source_tree": learner["source_tree"],
        },
        "WHAT_ORION_DISCOVERED": None,
        "WHAT_USEFUL_RELATIONSHIP_WAS_SUPPORTED": None,
        "WHY_THE_QUESTION_WAS_SELECTED": None,
        "WHAT_WAS_PREDICTED_AND_WHEN": None,
        "WHAT_ACTUALLY_HAPPENED": None,
        "HOW_THE_BASELINE_PERFORMED": None,
        "WHAT_ORION_REVISED_AND_WHY": None,
        "WHETHER_THE_REVISION_HELPED_LATER": None,
        "WHAT_REMAINED_UNKNOWN": [
            "No independently authored synthetic restaurant package and verified authorship record is available."
        ],
        "WHAT_EVIDENCE_WOULD_HELP_NEXT": [
            (
                "One package conforming to dataset.schema.json, fixed before execution and "
                f"bound to protocol SHA-256 {protocol_sha256}."
            ),
            "Independent reviewer evidence establishing preparer identity, prior engine/result exposure, retained evaluator-only material and freeze time."
        ],
        "WHAT_AUTHORIZATION_THAT_WOULD_REQUIRE": [
            "Separate synthetic metadata, historical-record, semantic-instrument and four staged outcome grants through existing admission interfaces."
        ],
        "WHAT_ORION_WAS_NOT_PERMITTED_TO_DO": [
            "Access customer systems or production networks.",
            "Read unreleased future records or evaluator-only commitments.",
            "Write to a business system, execute actions, change the frozen learner or promote a release gate."
        ],
        "LEARNING_LOOP_INTEGRITY": "BLOCKED",
        "INDEPENDENT_EVALUATION": "BLOCKED",
        "SEMANTIC_UNDERSTANDING": "NOT_PROVEN",
        "PREDICTIVE_IMPROVEMENT": "INSUFFICIENT_EVIDENCE",
        "ECONOMIC_VALUE": "NOT_PROVEN",
        "REAL_ORGANIZATION_GENERALIZATION": "NOT_PROVEN",
        "execution_allowed": False,
        "allow_live_customer_access": False,
        "LIVE_PILOT_READY": False,
        "candidate_host_qualification": "UNRESOLVED",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("status", "preflight", "run", "exercise", "freeze-receipt", "convert"),
    )
    parser.add_argument("package", nargs="?", type=Path)
    parser.add_argument(
        "--protocol",
        type=Path,
        default=Path("evaluation/frozen_learning_v1/protocol.json"),
    )
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--review-evidence", type=Path)
    parser.add_argument("--freeze-receipt", type=Path)
    parser.add_argument("--exposure-disclosure", type=Path)
    parser.add_argument("--generation-record", type=Path)
    parser.add_argument("--source-protocol", type=Path)
    parser.add_argument("--conversion-report", type=Path)
    parser.add_argument("--unknown-preparation-reason")
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--owner-report", type=Path)
    parser.add_argument(
        "--authorized-id", action="append",
        help=(
            "separately trusted authorization ID; repeat for every package reference "
            "when running or exercising"
        ),
    )
    parser.add_argument("--trusted-review-approval-sha256")
    arguments = parser.parse_args(argv)
    protocol_path = arguments.protocol.resolve()
    protocol = _load(protocol_path)
    if protocol.get("protocol_version") not in SUPPORTED_PROTOCOLS:
        raise ContractError("unsupported protocol version")
    protocol_sha256 = _digest_file(protocol_path)
    frozen = verify_frozen_learner(protocol, arguments.repository.resolve())
    if arguments.command == "status":
        if any(value is not None for value in (
            arguments.package, arguments.review_evidence, arguments.freeze_receipt,
            arguments.exposure_disclosure, arguments.generation_record,
            arguments.source_protocol, arguments.conversion_report,
            arguments.unknown_preparation_reason, arguments.state_dir,
            arguments.output, arguments.owner_report, arguments.authorized_id,
            arguments.trusted_review_approval_sha256,
        )):
            parser.error("status does not accept package, review, state or output paths")
        output = blocked_result(protocol, protocol_sha256)
        output["frozen_learner_verification"] = frozen
        print(json.dumps(output, sort_keys=True, indent=2))
        return 2
    if arguments.package is None:
        parser.error(f"{arguments.command} requires a package path")
    package_path = arguments.package.resolve()
    package = _load(package_path)
    if arguments.command == "convert":
        if protocol["protocol_version"] != PROTOCOL_VERSION_V2:
            parser.error("convert requires the v2 --protocol")
        if arguments.source_protocol is None or arguments.exposure_disclosure is None \
                or arguments.generation_record is None or arguments.output is None \
                or arguments.conversion_report is None \
                or arguments.unknown_preparation_reason is None:
            parser.error(
                "convert requires --source-protocol, --exposure-disclosure, "
                "--generation-record, --output, --conversion-report and "
                "--unknown-preparation-reason"
            )
        source_protocol = arguments.source_protocol.resolve()
        converted, report = convert_v1_package(
            package, original_package_sha256=_digest_file(package_path),
            protocol_sha256_v1=_digest_file(source_protocol),
            protocol_sha256_v2=protocol_sha256,
            exposure_disclosure_sha256=_digest_file(arguments.exposure_disclosure.resolve()),
            generation_record_sha256=_digest_file(arguments.generation_record.resolve()),
            unknown_preparation_reason=arguments.unknown_preparation_reason,
        )
        arguments.output.resolve().write_text(
            json.dumps(converted, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        arguments.conversion_report.resolve().write_text(
            json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(report, sort_keys=True, indent=2))
        return 0
    if arguments.command == "freeze-receipt":
        if protocol["protocol_version"] != PROTOCOL_VERSION_V2:
            parser.error("freeze-receipt requires the v2 --protocol")
        if arguments.exposure_disclosure is None or arguments.generation_record is None \
                or arguments.output is None:
            parser.error(
                "freeze-receipt requires --exposure-disclosure, --generation-record and --output"
            )
        validation = validate_package(
            package, protocol=protocol, protocol_sha256=protocol_sha256,
        )
        receipt = create_freeze_receipt_v2(
            package, validation, package_path=package_path,
            exposure_disclosure=arguments.exposure_disclosure.resolve(),
            generation_record=arguments.generation_record.resolve(),
        )
        arguments.output.resolve().write_text(
            json.dumps(receipt, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(receipt, sort_keys=True, indent=2))
        return 0
    if arguments.command == "preflight":
        if arguments.state_dir is not None or arguments.output is not None \
                or arguments.owner_report is not None:
            parser.error("preflight does not accept state or output paths")
        output = validate_package(
            package, protocol=protocol, protocol_sha256=protocol_sha256,
        )
        output["package_sha256"] = _digest_file(package_path)
        output["frozen_learner_verification"] = frozen
        if protocol["protocol_version"] == PROTOCOL_VERSION \
                and arguments.trusted_review_approval_sha256 is not None:
            parser.error("v1 preflight does not accept trusted review approval")
        if arguments.review_evidence is None:
            output["status"] = "BLOCKED_REVIEW_EVIDENCE_UNAVAILABLE"
            output["authorship_review"] = "NOT_PROVEN"
            print(json.dumps(output, sort_keys=True, indent=2))
            return 2
        if protocol["protocol_version"] == PROTOCOL_VERSION:
            output["authorship_review"] = verify_review_evidence(
                package, output, arguments.review_evidence.resolve())
            output["status"] = V1_VALIDATION_ONLY_STATUS
            output["execution_contract"] = "V2_REQUIRED"
            print(json.dumps(output, sort_keys=True, indent=2))
            return 0
        else:
            if arguments.freeze_receipt is None or arguments.exposure_disclosure is None \
                    or arguments.generation_record is None:
                raise ContractError(
                    "v2 review preflight requires freeze receipt, exposure disclosure "
                    "and generation record"
                )
            checked_at = datetime.now(UTC)
            receipt = verify_freeze_receipt_v2(
                package, output, receipt_path=arguments.freeze_receipt.resolve(),
                package_path=package_path,
                exposure_disclosure=arguments.exposure_disclosure.resolve(),
                generation_record=arguments.generation_record.resolve(),
                not_after=checked_at,
            )
            output["authorship_review"] = verify_review_evidence_v2(
                package, output, receipt=receipt,
                path=arguments.review_evidence.resolve(), run_start=checked_at,
            )
            if arguments.trusted_review_approval_sha256 is None:
                output["status"] = "BLOCKED_TRUSTED_REVIEW_APPROVAL_REQUIRED"
                output["review_execution_approval"] = "NOT_PROVIDED"
                print(json.dumps(output, sort_keys=True, indent=2))
                return 2
            output["review_execution_approval"] = _bind_trusted_review_approval(
                output["authorship_review"],
                arguments.trusted_review_approval_sha256,
            )
        output["status"] = "READY_FOR_SINGLE_FROZEN_EVALUATION"
        print(json.dumps(output, sort_keys=True, indent=2))
        return 0
    if arguments.state_dir is None:
        parser.error(f"{arguments.command} requires --state-dir")
    if arguments.authorized_id is None:
        parser.error(
            f"{arguments.command} requires at least one separately trusted --authorized-id"
        )
    independent = arguments.command == "run"
    if independent and arguments.review_evidence is None:
        parser.error("run requires --review-evidence")
    if independent and protocol["protocol_version"] == PROTOCOL_VERSION_V2 \
            and arguments.trusted_review_approval_sha256 is None:
        parser.error("v2 run requires --trusted-review-approval-sha256")
    if not independent and arguments.trusted_review_approval_sha256 is not None:
        parser.error("exercise does not accept --trusted-review-approval-sha256")
    result = execute_package(
        package, protocol=protocol, protocol_sha256=protocol_sha256,
        repository=arguments.repository.resolve(), state_dir=arguments.state_dir.resolve(),
        independent=independent, authorized_ids=frozenset(arguments.authorized_id),
        trusted_review_approval_sha256=arguments.trusted_review_approval_sha256,
        review_evidence=(arguments.review_evidence.resolve()
                         if arguments.review_evidence is not None else None),
        package_path=package_path,
        freeze_receipt=(arguments.freeze_receipt.resolve()
                        if arguments.freeze_receipt is not None else None),
        exposure_disclosure=(arguments.exposure_disclosure.resolve()
                             if arguments.exposure_disclosure is not None else None),
        generation_record=(arguments.generation_record.resolve()
                           if arguments.generation_record is not None else None),
    )
    result["package_sha256"] = _digest_file(package_path)
    encoded = json.dumps(result, sort_keys=True, indent=2) + "\n"
    if arguments.output is not None:
        arguments.output.resolve().write_text(encoded, encoding="utf-8")
    if arguments.owner_report is not None:
        arguments.owner_report.resolve().write_text(
            execution_owner_report(result), encoding="utf-8")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
