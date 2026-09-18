#!/usr/bin/env python3
"""Preflight the frozen ORION learning evaluation without exposing outcomes.

This utility verifies the frozen learner and an evaluator-supplied package. It
does not turn JSON into authority, issue grants, or execute a self-authored
fixture. A conforming package is an external prerequisite for the single run
through the existing governed adapters and organizational-cycle entry points.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from orion.business.fnb import FNB_RULES
from orion.business.fnb import VERSION as FNB_VERSION
from orion.learning.organizational_cycle import (
    BASELINE_MODEL,
    HORIZON,
    MAX_OUTCOME_RECORDS,
    REVISED_MODEL,
)
from orion.understanding.semantic_study import (
    AGGREGATE_FIELDS,
    ANCHOR_FIELDS,
    SEMANTIC_EVALUATOR_VERSION,
)

PROTOCOL_VERSION = "orion-frozen-learning-evaluation-v1"
PACKAGE_VERSION = "orion-independent-restaurant-dataset-v1"
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
    value: object, *, label: str, company: str, evidence_cutoff: datetime
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
        business_fields = set(batch["records"][0]) - {
            batch["identity_field"], batch["company_field"]
        }
        if not field_ids <= business_fields:
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
            if record["partition"] != company or record["subject_source"] != source \
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


def validate_package(
    package: dict[str, Any], *, protocol: dict[str, Any], protocol_sha256: str,
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
    content_identity = _digest_bytes(_canonical({
        "timeline": package["timeline"],
        "learner_inputs": package["learner_inputs"],
        "outcome_releases": package["outcome_releases"],
        "evaluator_only_commitment": package["evaluator_only_commitment"],
    }))
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
    if authorship["preparer_is_engine_implementer"] is not False \
            or authorship["shared_fixture_engine_authorship"] is not False \
            or authorship["answers_fixed_before_execution"] is not True:
        raise ContractError("dataset does not establish independent precommitted authorship")
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
    if review["status"] != "VERIFIED":
        raise ContractError("independent authorship review is not verified")
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
    learner = _exact(
        package["learner_inputs"], {"tenant_id", "company_id", "development", "evaluation"},
        "learner_inputs",
    )
    tenant = _opaque(learner["tenant_id"], "learner tenant")
    company = _opaque(learner["company_id"], "learner company")
    del tenant
    development = _validate_environment(
        learner["development"], label="development", company=company,
        evidence_cutoff=cutoff,
    )
    evaluation = _validate_environment(
        learner["evaluation"], label="evaluation", company=company,
        evidence_cutoff=cutoff,
    )
    if development["source_id"] == evaluation["source_id"] \
            or development["instrument_sources"] & evaluation["instrument_sources"]:
        raise ContractError("development and evaluation source domains must be distinct")
    if development["topology"] == evaluation["topology"]:
        raise ContractError("evaluation metadata must have a materially different topology")
    releases = _exact(
        package["outcome_releases"], {"development", "evaluation"}, "outcome_releases"
    )
    if not isinstance(releases["development"], list) or len(releases["development"]) != 2 \
            or not isinstance(releases["evaluation"], list) or len(releases["evaluation"]) != 2:
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
        "authorship_review": "VERIFIED",
        "release_cases": [case_id for case_id, _ in release_results],
        "economic_value_criterion_preregistered": criterion is not None,
        "execution_allowed": False,
        "allow_live_customer_access": False,
        "LIVE_PILOT_READY": False,
    }


def blocked_result(protocol: dict[str, Any], protocol_sha256: str) -> dict[str, object]:
    learner = protocol["frozen_learner"]
    return {
        "version": PROTOCOL_VERSION,
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
    parser.add_argument("command", choices=("status", "preflight"))
    parser.add_argument("package", nargs="?", type=Path)
    parser.add_argument(
        "--protocol",
        type=Path,
        default=Path("evaluation/frozen_learning_v1/protocol.json"),
    )
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    arguments = parser.parse_args(argv)
    protocol_path = arguments.protocol.resolve()
    protocol = _load(protocol_path)
    if protocol.get("protocol_version") != PROTOCOL_VERSION:
        raise ContractError("unsupported protocol version")
    protocol_sha256 = _digest_file(protocol_path)
    frozen = verify_frozen_learner(protocol, arguments.repository.resolve())
    if arguments.command == "status":
        if arguments.package is not None:
            parser.error("status does not accept a package")
        output = blocked_result(protocol, protocol_sha256)
        output["frozen_learner_verification"] = frozen
        print(json.dumps(output, sort_keys=True, indent=2))
        return 2
    if arguments.package is None:
        parser.error("preflight requires a package path")
    package_path = arguments.package.resolve()
    package = _load(package_path)
    output = validate_package(
        package, protocol=protocol, protocol_sha256=protocol_sha256
    )
    output["package_sha256"] = _digest_file(package_path)
    output["frozen_learner_verification"] = frozen
    print(json.dumps(output, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
