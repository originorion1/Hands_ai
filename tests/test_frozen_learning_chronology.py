import copy
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from frozen_learning_contract_fixture import refresh_dataset_identity, self_authored_package

import tools.frozen_learning_evaluation as evaluation
from tools.frozen_learning_evaluation import ContractError

ROOT = Path(__file__).resolve().parents[1]
V1_PROTOCOL_PATH = ROOT / "evaluation/frozen_learning_v1/protocol.json"
V2_PROTOCOL_PATH = ROOT / "evaluation/frozen_learning_v2/protocol.json"
V1_PROTOCOL_SHA256 = "92c5dcd63238dcd4429b3ea563b6413592e8277595f3be652db0fa13e5a746c1"


def _protocol(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return path


def _converted_package(tmp_path, *, independent=False):
    v1 = self_authored_package(V1_PROTOCOL_SHA256)
    v1_path = _write(tmp_path / "original-v1.json", v1)
    exposure = tmp_path / "exposure.txt"
    generation = tmp_path / "generation.txt"
    exposure.write_text("synthetic test disclosure\n", encoding="utf-8")
    generation.write_text("synthetic test generation record\n", encoding="utf-8")
    converted, report = evaluation.convert_v1_package(
        v1,
        original_package_sha256=evaluation._digest_file(v1_path),
        protocol_sha256_v1=V1_PROTOCOL_SHA256,
        protocol_sha256_v2=evaluation._digest_file(V2_PROTOCOL_PATH),
        exposure_disclosure_sha256=evaluation._digest_file(exposure),
        generation_record_sha256=evaluation._digest_file(generation),
        unknown_preparation_reason=(
            "The exact original preparation time was not retained in this synthetic test."
        ),
    )
    if independent:
        converted["authorship"]["prepared_by"] = "synthetic dataset author"
        converted["authorship"]["preparer_role"] = "test-only author role"
        converted["authorship"]["preparer_is_engine_implementer"] = False
        converted["authorship"]["shared_fixture_engine_authorship"] = False
        converted["authorship"]["independence_basis"] = (
            "Test-only declaration used to exercise fail-closed envelope checks; "
            "it is not independent evaluation evidence."
        )
    package_path = _write(tmp_path / "package-v2.json", converted)
    return v1, v1_path, converted, package_path, exposure, generation, report


def _receipt_and_review(tmp_path, package, package_path, exposure, generation):
    protocol = _protocol(V2_PROTOCOL_PATH)
    validation = evaluation.validate_package(
        package, protocol=protocol,
        protocol_sha256=evaluation._digest_file(V2_PROTOCOL_PATH),
        require_independent=True,
    )
    freeze_time = datetime(2040, 1, 1, tzinfo=UTC)
    receipt = evaluation.create_freeze_receipt_v2(
        package, validation, package_path=package_path,
        exposure_disclosure=exposure, generation_record=generation,
        observed_at=freeze_time,
    )
    receipt_path = _write(tmp_path / "freeze-receipt.json", receipt)
    review = {
        "review_version": evaluation.REVIEW_VERSION_V2,
        "protocol": {
            "version": evaluation.PROTOCOL_VERSION_V2,
            "sha256": validation["protocol_sha256"],
        },
        "package": {
            "version": evaluation.PACKAGE_VERSION_V2,
            "dataset_id": validation["dataset_id"],
            "material_sha256": validation["dataset_material_sha256"],
        },
        "freeze_receipt_sha256": evaluation._digest_file(receipt_path),
        "freeze_observed_at": freeze_time.isoformat(),
        "prepared_by": package["authorship"]["prepared_by"],
        "preparer_role": package["authorship"]["preparer_role"],
        "reviewed_by": "synthetic separate reviewer",
        "reviewer_role": "test-only review role",
        "reviewed_at": (freeze_time + timedelta(hours=1)).isoformat(),
        "review_scope": "Test-only validation of chronology and digest binding.",
        "decision": "VERIFIED",
        "independence_basis": "Distinct test labels do not prove real independence.",
        "identity_authentication": "NOT_PROVEN_BY_DIGEST_ALONE",
    }
    review_path = _write(tmp_path / "review.json", review)
    return validation, receipt_path, review_path, freeze_time


def test_v1_contract_bytes_and_validation_remain_unchanged():
    assert evaluation._digest_file(V1_PROTOCOL_PATH) == V1_PROTOCOL_SHA256
    package = self_authored_package(V1_PROTOCOL_SHA256)
    result = evaluation.validate_package(
        package, protocol=_protocol(V1_PROTOCOL_PATH),
        protocol_sha256=V1_PROTOCOL_SHA256, require_independent=False,
    )
    assert result["protocol_version"] == evaluation.PROTOCOL_VERSION
    assert result["dataset_id"] == package["dataset_id"]


def test_v2_separates_historical_synthetic_time_from_later_real_receipt(tmp_path):
    _, _, package, package_path, exposure, generation, _ = _converted_package(
        tmp_path, independent=True
    )
    validation, receipt_path, review_path, freeze_time = _receipt_and_review(
        tmp_path, package, package_path, exposure, generation
    )
    receipt = evaluation.verify_freeze_receipt_v2(
        package, validation, receipt_path=receipt_path, package_path=package_path,
        exposure_disclosure=exposure, generation_record=generation,
        not_after=freeze_time + timedelta(hours=2),
    )
    reviewed = evaluation.verify_review_evidence_v2(
        package, validation, receipt=receipt, path=review_path,
        run_start=freeze_time + timedelta(hours=2),
    )

    assert package["timeline"]["evaluation_clock_start"].startswith("2031-")
    assert receipt["observed_at"].year == 2040
    assert reviewed["status"] == "AVAILABLE_AND_PACKAGE_BOUND"
    assert validation["original_preparation"]["status"] == "UNKNOWN"
    assert validation["authorship_review"] == "PENDING_SEPARATE_ENVELOPE"
    assert reviewed["identity_authentication"] == "NOT_PROVEN_BY_DIGEST_ALONE"


def test_unknown_original_preparation_forbids_a_timestamp(tmp_path):
    _, _, package, _, _, _, _ = _converted_package(tmp_path)
    package["authorship"]["original_preparation"]["timestamp"] = "2030-01-01T00:00:00+00:00"
    with pytest.raises(ContractError, match="must not invent a timestamp"):
        evaluation.validate_package(
            package, protocol=_protocol(V2_PROTOCOL_PATH),
            protocol_sha256=evaluation._digest_file(V2_PROTOCOL_PATH),
            require_independent=False,
        )


@pytest.mark.parametrize(
    ("package_version", "protocol_path"),
    [
        (evaluation.PACKAGE_VERSION, V2_PROTOCOL_PATH),
        (evaluation.PACKAGE_VERSION_V2, V1_PROTOCOL_PATH),
        ("orion-independent-restaurant-dataset-v3", V2_PROTOCOL_PATH),
    ],
)
def test_mixed_or_unsupported_versions_fail_closed(tmp_path, package_version, protocol_path):
    _, _, package, _, _, _, _ = _converted_package(tmp_path)
    package["package_version"] = package_version
    with pytest.raises(ContractError, match="mixed|unsupported"):
        evaluation.validate_package(
            package, protocol=_protocol(protocol_path),
            protocol_sha256=evaluation._digest_file(protocol_path),
            require_independent=False,
        )


def test_conversion_preserves_operational_material_and_oracle_and_retains_original(tmp_path):
    v1, v1_path, package, _, _, _, report = _converted_package(tmp_path)

    assert v1_path.exists()
    assert v1["package_version"] == evaluation.PACKAGE_VERSION
    assert package["package_version"] == evaluation.PACKAGE_VERSION_V2
    assert package["dataset_id"] != v1["dataset_id"]
    assert report["operational_material_identical"] is True
    assert report["material_sha256_before"] == report["material_sha256_after"]
    assert package["evaluator_only_commitment"] == v1["evaluator_only_commitment"]
    assert package["timeline"] == v1["timeline"]
    assert package["learner_inputs"] == v1["learner_inputs"]
    assert package["outcome_releases"] == v1["outcome_releases"]
    assert report["original_artifact_overwritten"] is False
    assert package["authorship"]["legacy_source"]["package_sha256"] == hashlib.sha256(
        v1_path.read_bytes()
    ).hexdigest()


def test_receipt_rejects_modified_material_or_commitment(tmp_path):
    _, _, package, package_path, exposure, generation, _ = _converted_package(
        tmp_path, independent=True
    )
    validation, receipt_path, _, freeze_time = _receipt_and_review(
        tmp_path, package, package_path, exposure, generation
    )
    changed = copy.deepcopy(package)
    changed["evaluator_only_commitment"]["sealed_expected_results_sha256"] = "f" * 64
    changed_path = _write(tmp_path / "changed.json", changed)

    with pytest.raises(ContractError, match="exact raw package and material"):
        evaluation.verify_freeze_receipt_v2(
            changed, validation, receipt_path=receipt_path, package_path=changed_path,
            exposure_disclosure=exposure, generation_record=generation,
            not_after=freeze_time + timedelta(hours=2),
        )

    forged = json.loads(receipt_path.read_text(encoding="utf-8"))
    forged["package"]["sha256"] = "0" * 64
    forged_path = _write(tmp_path / "forged-receipt.json", forged)
    with pytest.raises(ContractError, match="exact raw package and material"):
        evaluation.verify_freeze_receipt_v2(
            package, validation, receipt_path=forged_path, package_path=package_path,
            exposure_disclosure=exposure, generation_record=generation,
            not_after=freeze_time + timedelta(hours=2),
        )


def test_missing_or_mismatched_receipt_and_review_chronology_deny_before_source_io(
    tmp_path, monkeypatch
):
    _, _, package, package_path, exposure, generation, _ = _converted_package(
        tmp_path, independent=True
    )
    validation, receipt_path, review_path, freeze_time = _receipt_and_review(
        tmp_path, package, package_path, exposure, generation
    )
    source_calls = 0

    def forbidden_source_io(*args, **kwargs):
        nonlocal source_calls
        source_calls += 1
        raise AssertionError("source I/O must not occur")

    monkeypatch.setattr(evaluation, "_discover_environment", forbidden_source_io)
    with pytest.raises(ContractError, match="requires .*package_path"):
        evaluation.execute_package(
            package, protocol=_protocol(V2_PROTOCOL_PATH),
            protocol_sha256=evaluation._digest_file(V2_PROTOCOL_PATH),
            repository=ROOT, state_dir=tmp_path / "state-missing", independent=True,
            authorized_ids=evaluation.package_authorization_references(package),
            trusted_review_approval_sha256=None,
            review_evidence=review_path,
        )
    assert source_calls == 0

    review = json.loads(review_path.read_text(encoding="utf-8"))
    review["reviewed_at"] = (freeze_time - timedelta(seconds=1)).isoformat()
    _write(review_path, review)
    with pytest.raises(ContractError, match="chronology is invalid"):
        evaluation.execute_package(
            package, protocol=_protocol(V2_PROTOCOL_PATH),
            protocol_sha256=evaluation._digest_file(V2_PROTOCOL_PATH),
            repository=ROOT, state_dir=tmp_path / "state-inverted", independent=True,
            authorized_ids=evaluation.package_authorization_references(package),
            trusted_review_approval_sha256=None,
            review_evidence=review_path, package_path=package_path,
            freeze_receipt=receipt_path, exposure_disclosure=exposure,
            generation_record=generation,
            controller_clock=lambda: freeze_time + timedelta(hours=2),
        )
    assert source_calls == 0
    assert validation["status"] == "READY_FOR_SINGLE_FROZEN_EVALUATION"

    _, receipt_path, review_path, _ = _receipt_and_review(
        tmp_path, package, package_path, exposure, generation
    )
    state_dir = tmp_path / "state-valid-envelope"
    with pytest.raises(ContractError, match="separately trusted review approval"):
        evaluation.execute_package(
            package, protocol=_protocol(V2_PROTOCOL_PATH),
            protocol_sha256=evaluation._digest_file(V2_PROTOCOL_PATH),
            repository=ROOT, state_dir=state_dir, independent=True,
            authorized_ids=evaluation.package_authorization_references(package),
            trusted_review_approval_sha256=None,
            review_evidence=review_path, package_path=package_path,
            freeze_receipt=receipt_path, exposure_disclosure=exposure,
            generation_record=generation,
            controller_clock=lambda: freeze_time + timedelta(hours=2),
        )
    assert source_calls == 0
    assert not state_dir.exists()

    with pytest.raises(ContractError, match="does not bind the review artifact"):
        evaluation.execute_package(
            package, protocol=_protocol(V2_PROTOCOL_PATH),
            protocol_sha256=evaluation._digest_file(V2_PROTOCOL_PATH),
            repository=ROOT, state_dir=state_dir, independent=True,
            authorized_ids=evaluation.package_authorization_references(package),
            trusted_review_approval_sha256="0" * 64,
            review_evidence=review_path, package_path=package_path,
            freeze_receipt=receipt_path, exposure_disclosure=exposure,
            generation_record=generation,
            controller_clock=lambda: freeze_time + timedelta(hours=2),
        )
    assert source_calls == 0
    assert not state_dir.exists()

    with pytest.raises(AssertionError, match="source I/O must not occur"):
        evaluation.execute_package(
            package, protocol=_protocol(V2_PROTOCOL_PATH),
            protocol_sha256=evaluation._digest_file(V2_PROTOCOL_PATH),
            repository=ROOT, state_dir=state_dir, independent=True,
            authorized_ids=evaluation.package_authorization_references(package),
            trusted_review_approval_sha256=evaluation._digest_file(review_path),
            review_evidence=review_path, package_path=package_path,
            freeze_receipt=receipt_path, exposure_disclosure=exposure,
            generation_record=generation,
            controller_clock=lambda: freeze_time + timedelta(hours=2),
        )
    assert source_calls == 1
    controller_record = json.loads(
        (state_dir / "controller-run.json").read_text(encoding="utf-8")
    )
    assert controller_record["run_started_at"] == (
        freeze_time + timedelta(hours=2)
    ).isoformat()
    assert controller_record["trusted_review_approval"] == {
        "status": "SEPARATELY_TRUSTED_EXACT_REVIEW_DIGEST",
        "review_evidence_sha256": evaluation._digest_file(review_path),
    }


def test_explicitly_approved_synthetic_v2_execution_completes(tmp_path):
    _, _, package, package_path, exposure, generation, _ = _converted_package(
        tmp_path, independent=True
    )
    _, receipt_path, review_path, freeze_time = _receipt_and_review(
        tmp_path, package, package_path, exposure, generation
    )

    result = evaluation.execute_package(
        package, protocol=_protocol(V2_PROTOCOL_PATH),
        protocol_sha256=evaluation._digest_file(V2_PROTOCOL_PATH),
        repository=ROOT, state_dir=tmp_path / "state", independent=True,
        authorized_ids=evaluation.package_authorization_references(package),
        trusted_review_approval_sha256=evaluation._digest_file(review_path),
        review_evidence=review_path, package_path=package_path,
        freeze_receipt=receipt_path, exposure_disclosure=exposure,
        generation_record=generation,
        controller_clock=lambda: freeze_time + timedelta(hours=2),
    )

    assert result["status"] == "COMPLETED"
    assert result["authorship_review"]["identity_authentication"] == (
        "NOT_PROVEN_BY_DIGEST_ALONE"
    )
    assert result["authorship_review"]["execution_approval"] == {
        "status": "SEPARATELY_TRUSTED_EXACT_REVIEW_DIGEST",
        "review_evidence_sha256": evaluation._digest_file(review_path),
    }
    assert result["execution_allowed"] is False
    assert result["allow_live_customer_access"] is False
    assert result["LIVE_PILOT_READY"] is False


def test_future_freeze_naive_review_and_early_release_fail_closed(tmp_path):
    _, _, package, package_path, exposure, generation, _ = _converted_package(
        tmp_path, independent=True
    )
    validation, receipt_path, review_path, freeze_time = _receipt_and_review(
        tmp_path, package, package_path, exposure, generation
    )
    with pytest.raises(ContractError, match="in the future"):
        evaluation.verify_freeze_receipt_v2(
            package, validation, receipt_path=receipt_path, package_path=package_path,
            exposure_disclosure=exposure, generation_record=generation,
            not_after=freeze_time - timedelta(seconds=1),
        )

    receipt = evaluation.verify_freeze_receipt_v2(
        package, validation, receipt_path=receipt_path, package_path=package_path,
        exposure_disclosure=exposure, generation_record=generation,
        not_after=freeze_time + timedelta(hours=2),
    )
    review = json.loads(review_path.read_text(encoding="utf-8"))
    review["reviewed_at"] = (freeze_time + timedelta(hours=3)).isoformat()
    _write(review_path, review)
    with pytest.raises(ContractError, match="chronology is invalid"):
        evaluation.verify_review_evidence_v2(
            package, validation, receipt=receipt, path=review_path,
            run_start=freeze_time + timedelta(hours=2),
        )

    review["reviewed_at"] = "2040-01-01T01:00:00"
    _write(review_path, review)
    with pytest.raises(ContractError, match="UTC offset"):
        evaluation.verify_review_evidence_v2(
            package, validation, receipt=receipt, path=review_path,
            run_start=freeze_time + timedelta(hours=2),
        )

    early = copy.deepcopy(package)
    early["outcome_releases"]["development"][0]["available_at"] = (
        datetime.fromisoformat(early["timeline"]["evaluation_clock_start"])
        + timedelta(hours=23)
    ).isoformat()
    early["dataset_id"] = evaluation._v2_dataset_id(early)
    with pytest.raises(ContractError, match="frozen one-day horizon"):
        evaluation.validate_package(
            early, protocol=_protocol(V2_PROTOCOL_PATH),
            protocol_sha256=evaluation._digest_file(V2_PROTOCOL_PATH),
            require_independent=True,
        )


def test_v2_keeps_frozen_learner_and_scoring_identity():
    v1 = _protocol(V1_PROTOCOL_PATH)
    v2 = _protocol(V2_PROTOCOL_PATH)

    assert v2["frozen_learner"] == v1["frozen_learner"]
    assert evaluation.verify_frozen_learner(v2, ROOT) == evaluation.verify_frozen_learner(
        v1, ROOT
    )


def test_v2_contract_documents_are_machine_readable_and_version_pinned():
    dataset_schema = _protocol(
        ROOT / "evaluation/frozen_learning_v2/dataset.schema.json"
    )
    receipt_schema = _protocol(
        ROOT / "evaluation/frozen_learning_v2/freeze-receipt.schema.json"
    )
    review_schema = _protocol(
        ROOT / "evaluation/frozen_learning_v2/review.schema.json"
    )

    assert dataset_schema["properties"]["package_version"]["const"] == (
        evaluation.PACKAGE_VERSION_V2
    )
    assert receipt_schema["properties"]["freeze_receipt_version"]["const"] == (
        evaluation.FREEZE_RECEIPT_VERSION_V2
    )
    assert review_schema["properties"]["review_version"]["const"] == (
        evaluation.REVIEW_VERSION_V2
    )
    assert dataset_schema["properties"]["timeline"]["$ref"].startswith(
        "../frozen_learning_v1/"
    )


def test_v2_accepts_declared_technical_metadata_without_changing_v1(tmp_path):
    v1 = self_authored_package(V1_PROTOCOL_SHA256)
    for phase in ("development", "evaluation"):
        environment = v1["learner_inputs"][phase]
        environment["source_id"] += "/"
        for instrument in environment["instruments"]:
            for record in instrument["records"]:
                record["subject_source"] += "/"
        for resource in environment["resources"]:
            batch = resource["historical_batch"]
            resource["fields"].extend((
                {"id": batch["identity_field"], "kind": "Link"},
                {"id": batch["company_field"], "kind": "Link"},
            ))
    refresh_dataset_identity(v1)
    with pytest.raises(ContractError, match="historical records omit discovered fields"):
        evaluation.validate_package(
            v1, protocol=_protocol(V1_PROTOCOL_PATH),
            protocol_sha256=V1_PROTOCOL_SHA256, require_independent=False,
        )

    v1_path = _write(tmp_path / "technical-fields-v1.json", v1)
    exposure = tmp_path / "exposure.txt"
    generation = tmp_path / "generation.txt"
    exposure.write_text("synthetic exposure\n", encoding="utf-8")
    generation.write_text("synthetic generation\n", encoding="utf-8")
    v2, _ = evaluation.convert_v1_package(
        v1, original_package_sha256=evaluation._digest_file(v1_path),
        protocol_sha256_v1=V1_PROTOCOL_SHA256,
        protocol_sha256_v2=evaluation._digest_file(V2_PROTOCOL_PATH),
        exposure_disclosure_sha256=evaluation._digest_file(exposure),
        generation_record_sha256=evaluation._digest_file(generation),
        unknown_preparation_reason="Synthetic regression does not retain an exact time.",
    )

    result = evaluation.validate_package(
        v2, protocol=_protocol(V2_PROTOCOL_PATH),
        protocol_sha256=evaluation._digest_file(V2_PROTOCOL_PATH),
        require_independent=False,
    )
    assert result["status"] == "READY_FOR_SINGLE_FROZEN_EVALUATION"
