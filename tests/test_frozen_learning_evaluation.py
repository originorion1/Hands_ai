import hashlib
import json
from pathlib import Path

import pytest

from tools.frozen_learning_evaluation import (
    ContractError,
    blocked_result,
    validate_package,
    verify_frozen_learner,
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
    ]["const"] is False
    assert schema["properties"]["authorship"]["properties"]["review"]["properties"][
        "status"
    ]["const"] == "VERIFIED"


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
