import copy
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from frozen_learning_contract_fixture import (
    refresh_dataset_identity,
    self_authored_package,
)

from orion.learning.organizational_cycle import (
    OutcomeQuery,
    begin_learning_cycle,
    select_prediction_question,
)
from orion.learning.prediction_ledger import BusinessCohort
from tools.frozen_learning_evaluation import (
    ContractError,
    _discover_environment,
    _StagedReleaseController,
    blocked_result,
    execute_package,
    package_authorization_references,
    validate_package,
    verify_frozen_learner,
    verify_review_evidence,
)
from tools.frozen_learning_evaluation import _canonical as canonical
from tools.frozen_learning_evaluation import _digest_file as digest_file
from tools.frozen_learning_evaluation import _validate_batch as validate_batch

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_PATH = ROOT / "evaluation/frozen_learning_v1/protocol.json"
SCHEMA_PATH = ROOT / "evaluation/frozen_learning_v1/dataset.schema.json"


def protocol():
    return json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))


def test_protocol_freezes_exact_pr181_learner_components():
    frozen = verify_frozen_learner(protocol(), ROOT)

    assert frozen == {
        "source_commit": "7ab613ebe6ea4fdd0ab16a2e2d60baf23d135d62",
        "source_tree": "73776d74d32d9de72b528740aa977ba6d673daa8",
        "component_count": 11,
        "component_hashes_match": True,
        "semantic_evaluator_version": "semantic-rules-v1",
        "fnb_assessment_version": "fnb-sample-assessment-v1",
        "fnb_rules_sha256": "48469b14a295c47249b13df11b104b70fd6fe0ef9b7feef7ea0fdf34ebba1c4e",
        "baseline_model": "frozen-laplace-prior-v1",
        "revised_model": "resolved-laplace-revision-v1",
        "horizon_seconds": 86400,
        "maximum_records_per_batch": 100,
    }


def test_missing_independent_material_is_blocked_without_product_claims():
    result = blocked_result(protocol(), digest_file(PROTOCOL_PATH))
    retained = json.loads(
        (ROOT / "evaluation/frozen_learning_v1/result.json").read_text(encoding="utf-8")
    )

    assert retained == result
    assert result["status"] == "BLOCKED"
    assert result["dataset_identity"] is None
    assert result["LEARNING_LOOP_INTEGRITY"] == "BLOCKED"
    assert result["INDEPENDENT_EVALUATION"] == "BLOCKED"
    assert result["SEMANTIC_UNDERSTANDING"] == "NOT_PROVEN"
    assert result["PREDICTIVE_IMPROVEMENT"] == "INSUFFICIENT_EVIDENCE"
    assert result["ECONOMIC_VALUE"] == "NOT_PROVEN"
    assert result["REAL_ORGANIZATION_GENERALIZATION"] == "NOT_PROVEN"
    assert result["execution_allowed"] is False
    assert result["allow_live_customer_access"] is False
    assert result["LIVE_PILOT_READY"] is False
    assert result["WHAT_ORION_DISCOVERED"] is None
    assert result["WHETHER_THE_REVISION_HELPED_LATER"] is None


def test_schema_is_machine_readable_and_forbids_undeclared_top_level_material():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "package_version",
        "dataset_id",
        "protocol",
        "authorship",
        "timeline",
        "learner_inputs",
        "outcome_releases",
        "evaluator_only_commitment",
    }
    assert schema["properties"]["authorship"]["properties"][
        "shared_fixture_engine_authorship"
    ]["type"] == "boolean"
    assert schema["properties"]["authorship"]["properties"]["review"]["properties"][
        "status"
    ]["enum"] == ["VERIFIED", "NOT_PROVEN"]


def test_record_preflight_rejects_precomputed_answers():
    token = "a" * 16
    with pytest.raises(ContractError, match="evaluator answer or precomputed total"):
        validate_batch(
            {
                "resource_id": f"r_{token}",
                "authorization_id": f"g_{token}",
                "provenance_source": f"p_{token}",
                "identity_field": f"i_{token}",
                "company_field": f"c_{token}",
                "date_field": f"d_{token}",
                "records": [{
                    f"i_{token}": "record-1",
                    f"c_{token}": f"o_{token}",
                    f"d_{token}": "2032-01-02",
                    "precomputed_total": 150,
                }],
            },
            label="release",
            company=f"o_{token}",
        )


def test_self_authored_or_unreviewed_package_cannot_be_qualified():
    current_protocol = protocol()
    protocol_sha256 = digest_file(PROTOCOL_PATH)
    staged = {
        "timeline": {},
        "learner_inputs": {},
        "outcome_releases": {},
        "evaluator_only_commitment": {},
    }
    content_identity = hashlib.sha256(canonical(staged)).hexdigest()
    package = {
        "package_version": "orion-independent-restaurant-dataset-v1",
        "dataset_id": "dataset-" + content_identity[:32],
        "protocol": {
            "version": "orion-frozen-learning-evaluation-v1",
            "sha256": protocol_sha256,
        },
        "authorship": {
            "prepared_by": "engine implementer",
            "preparer_role": "implementation",
            "prepared_at": "2031-01-01T00:00:00+00:00",
            "fixed_at": "2031-01-01T01:00:00+00:00",
            "independence_basis": "none",
            "engine_source_seen_before_fixing": True,
            "engine_source_seen_details": "all frozen learner source",
            "engine_results_seen_before_fixing": True,
            "engine_results_seen_details": "the prior self-authored fixture results",
            "preparer_is_engine_implementer": True,
            "shared_fixture_engine_authorship": True,
            "answers_fixed_before_execution": True,
            "learner_visible_material": ["opaque metadata"],
            "evaluator_retained_material": ["expected results"],
            "review": {
                "status": "PENDING",
                "reviewed_by": "none",
                "reviewer_role": "none",
                "reviewed_at": "2031-01-01T02:00:00+00:00",
                "evidence_sha256": "0" * 64,
            },
        },
        **staged,
    }

    with pytest.raises(ContractError, match="independent precommitted authorship"):
        validate_package(
            package, protocol=current_protocol, protocol_sha256=protocol_sha256
        )


def test_self_authored_fixture_executes_bridge_without_independence_claim(tmp_path):
    current_protocol = protocol()
    protocol_sha256 = digest_file(PROTOCOL_PATH)
    package = self_authored_package(protocol_sha256)

    result = execute_package(
        package, protocol=current_protocol, protocol_sha256=protocol_sha256,
        repository=ROOT, state_dir=tmp_path / "state", independent=False,
        authorized_ids=package_authorization_references(package),
        trusted_review_approval_sha256=None,
    )

    assert result["status"] == "INFRASTRUCTURE_VERIFIED"
    assert result["evaluation_mode"] == "SELF_AUTHORED_INFRASTRUCTURE"
    assert result["LEARNING_LOOP_INTEGRITY"] == "PASS"
    assert result["INDEPENDENT_EVALUATION"] == "BLOCKED"
    assert result["SEMANTIC_UNDERSTANDING"] == "SUPPORTED_WITHIN_TESTED_SCOPE"
    assert result["PREDICTIVE_IMPROVEMENT"] == "NOT_DEMONSTRATED"
    assert result["ECONOMIC_VALUE"] == "NOT_PROVEN"
    assert result["REAL_ORGANIZATION_GENERALIZATION"] == "NOT_PROVEN"
    assert result["evaluator_only_material_in_learner_state"] is False
    assert result["outcome_source_io_count"] == 6
    assert [item["case_id"] for item in result["release_checks"]] == [
        "development-1", "development-2", "evaluation-1", "evaluation-2",
    ]
    assert all(item["prediction_committed_before_release"]
               for item in result["release_checks"])
    assert [len(item["prediction_ids"]) for item in result["release_checks"]] == [1, 1, 2, 2]
    learning = result["learning_cycle"]
    assert [item["normalized_total"] for item in
            learning["development"]["operational_outcomes"]] == ["150", "130"]
    assert [item["normalized_total"] for item in
            learning["evaluation"]["operational_outcomes"]] == ["100", "140"]
    assert all(item["record_provenance"] for item in (
        *learning["development"]["operational_outcomes"],
        *learning["evaluation"]["operational_outcomes"],
    ))
    assert learning["evaluation"]["prior_brier"] == pytest.approx(0.25)
    assert learning["evaluation"]["revised_brier"] == pytest.approx(0.3125)
    assert learning["failed_prediction_cases"] == ("evaluation-1",)
    assert result["frozen_learner_before"] == result["frozen_learner_after"]
    assert result["execution_allowed"] is False
    assert result["allow_live_customer_access"] is False
    assert result["LIVE_PILOT_READY"] is False


def test_execution_requires_exact_separately_trusted_authorizations_before_io(
    tmp_path, monkeypatch
):
    protocol_sha256 = digest_file(PROTOCOL_PATH)
    package = self_authored_package(protocol_sha256)
    references = package_authorization_references(package)
    source_calls = 0

    def forbidden_source_io(*args, **kwargs):
        nonlocal source_calls
        source_calls += 1
        raise AssertionError("source I/O must not occur")

    monkeypatch.setattr(
        "tools.frozen_learning_evaluation._discover_environment", forbidden_source_io
    )
    with pytest.raises(ContractError, match="separately trusted authorization IDs"):
        execute_package(
            package, protocol=protocol(), protocol_sha256=protocol_sha256,
            repository=ROOT, state_dir=tmp_path / "state-omitted", independent=False,
            authorized_ids=None,
            trusted_review_approval_sha256=None,
        )
    assert source_calls == 0
    assert not (tmp_path / "state-omitted").exists()

    forged = copy.deepcopy(package)
    forged["learner_inputs"]["development"]["metadata_authorization_id"] = (
        "g_ffffffffffffffffffffffffffffffff"
    )
    refresh_dataset_identity(forged)
    with pytest.raises(ContractError, match="must exactly match package references"):
        execute_package(
            forged, protocol=protocol(), protocol_sha256=protocol_sha256,
            repository=ROOT, state_dir=tmp_path / "state-forged", independent=False,
            authorized_ids=references,
            trusted_review_approval_sha256=None,
        )
    assert source_calls == 0
    assert not (tmp_path / "state-forged").exists()


def _committed_controller(package, tmp_path):
    tenant = package["learner_inputs"]["tenant_id"]
    company = package["learner_inputs"]["company_id"]
    cutoff = datetime.fromisoformat(package["timeline"]["discovery_evidence_cutoff"])
    start = datetime.fromisoformat(package["timeline"]["evaluation_clock_start"])
    authorized = package_authorization_references(package)
    calls = []
    development = _discover_environment(
        package["learner_inputs"]["development"], tenant=tenant, company=company,
        evidence_time=cutoff, authorized_ids=authorized, calls=calls,
    )
    evaluation = _discover_environment(
        package["learner_inputs"]["evaluation"], tenant=tenant, company=company,
        evidence_time=cutoff, authorized_ids=authorized, calls=calls,
    )
    ledger = tmp_path / "predictions.sqlite3"
    state = {"now": start}
    started = begin_learning_cycle(
        development, evaluation, ledger_path=ledger, clock=lambda: state["now"],
    )
    question = select_prediction_question(development)
    query = OutcomeQuery(
        "development-1", question.target_definition, question.unit,
        BusinessCohort(tenant, company, start, start + timedelta(days=1)),
    )
    controller = _StagedReleaseController(
        phase="development",
        releases=tuple(package["outcome_releases"]["development"]),
        ledger_path=ledger, tenant=tenant, target=question.target_definition,
        unit=question.unit, company=company, state=state,
        authorized_ids=authorized, calls=calls,
    )
    assert started["prediction_id"]
    return controller, query, calls, authorized


def test_release_gate_denies_mismatch_early_release_and_missing_grant_before_io(tmp_path):
    package = self_authored_package(digest_file(PROTOCOL_PATH))
    controller, query, calls, authorized = _committed_controller(package, tmp_path)
    before = len(calls)

    mismatched = OutcomeQuery("development-2", query.target_definition, query.unit, query.cohort)
    with pytest.raises(ContractError, match="does not match the requested case"):
        controller.acquire((mismatched,))
    assert len(calls) == before

    controller.state["now"] = query.horizon_end
    with pytest.raises(ContractError, match="not prospective"):
        controller.acquire((query,))
    assert len(calls) == before

    controller.state["now"] = datetime(2031, 1, 1, tzinfo=UTC)
    first_grant = package["outcome_releases"]["development"][0]["batches"][0][
        "authorization_id"
    ]
    controller.authorized_ids = frozenset(set(authorized) - {first_grant})
    with pytest.raises(ValueError, match="authorization missing or ambiguous"):
        controller.acquire((query,))
    assert len(calls) == before


def test_unsupported_semantics_remain_unknown_without_outcome_release(tmp_path):
    protocol_sha256 = digest_file(PROTOCOL_PATH)
    package = self_authored_package(protocol_sha256)
    for phase in ("development", "evaluation"):
        for instrument in package["learner_inputs"][phase]["instruments"]:
            retained = [
                (record, origin)
                for record, origin in zip(
                    instrument["records"], instrument["origins"], strict=True,
                )
                if record["dimension"] != "identity"
            ]
            instrument["records"] = [record for record, _ in retained]
            instrument["origins"] = [origin for _, origin in retained]
    refresh_dataset_identity(package)

    result = execute_package(
        package, protocol=protocol(), protocol_sha256=protocol_sha256,
        repository=ROOT, state_dir=tmp_path / "state", independent=False,
        authorized_ids=package_authorization_references(package),
        trusted_review_approval_sha256=None,
    )

    assert result["status"] == "UNKNOWN"
    assert result["SEMANTIC_UNDERSTANDING"] == "UNKNOWN"
    assert result["PREDICTIVE_IMPROVEMENT"] == "INSUFFICIENT_EVIDENCE"
    assert result["outcome_source_io_count"] == 0


def test_independent_execution_requires_available_review_evidence(tmp_path):
    package = self_authored_package(digest_file(PROTOCOL_PATH))
    package["authorship"]["preparer_is_engine_implementer"] = False
    package["authorship"]["shared_fixture_engine_authorship"] = False
    package["authorship"]["review"]["status"] = "VERIFIED"

    with pytest.raises(ContractError, match="available review evidence"):
        execute_package(
            package, protocol=protocol(), protocol_sha256=digest_file(PROTOCOL_PATH),
            repository=ROOT, state_dir=tmp_path / "state", independent=True,
            authorized_ids=package_authorization_references(package),
            trusted_review_approval_sha256=None,
        )


def test_review_evidence_must_be_available_and_bound(tmp_path):
    protocol_sha256 = digest_file(PROTOCOL_PATH)
    package = self_authored_package(protocol_sha256)
    validation = validate_package(
        package, protocol=protocol(), protocol_sha256=protocol_sha256,
        require_independent=False,
    )
    review = package["authorship"]["review"]
    document = {
        "review_version": "orion-independent-review-evidence-v1",
        "dataset_id": validation["dataset_id"],
        "protocol_sha256": protocol_sha256,
        "dataset_material_sha256": validation["dataset_material_sha256"],
        "prepared_by": package["authorship"]["prepared_by"],
        "fixed_at": package["authorship"]["fixed_at"],
        "reviewed_by": review["reviewed_by"],
        "reviewed_at": review["reviewed_at"],
        "review_scope": "Contract fixture mechanics only; independence remains not proven.",
    }
    path = tmp_path / "review.json"
    encoded = json.dumps(document, sort_keys=True, indent=2) + "\n"
    path.write_text(encoded, encoding="utf-8")
    review["evidence_sha256"] = hashlib.sha256(encoded.encode()).hexdigest()

    result = verify_review_evidence(package, validation, path)
    assert result["status"] == "AVAILABLE_AND_PACKAGE_BOUND"
    assert result["identity_authentication"] == "NOT_PROVEN_BY_DIGEST_ALONE"

    document["reviewed_by"] = "different unbound reviewer"
    path.write_text(json.dumps(document, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(ContractError, match="reviewed_by does not bind"):
        verify_review_evidence(package, validation, path)


def test_engine_hash_drift_blocks_execution(tmp_path):
    current = protocol()
    for relative in current["frozen_learner"]["component_sha256"]:
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())
    drifted = next(iter(current["frozen_learner"]["component_sha256"]))
    (tmp_path / drifted).write_text("drift", encoding="utf-8")

    with pytest.raises(ContractError, match="frozen learner component mismatch"):
        execute_package(
            self_authored_package(digest_file(PROTOCOL_PATH)), protocol=current,
            protocol_sha256=digest_file(PROTOCOL_PATH), repository=tmp_path,
            state_dir=tmp_path / "state", independent=False,
            authorized_ids=frozenset(),
            trusted_review_approval_sha256=None,
        )
