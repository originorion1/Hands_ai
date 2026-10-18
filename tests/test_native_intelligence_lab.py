"""Offline split, equal-read and evidence-integrity regression contracts."""

import hashlib
import json
from pathlib import Path

import pytest

from evaluation.native_intelligence_v1.benchmark import calibrate, run_benchmark, run_case
from evaluation.native_intelligence_v1.generate_lab import SPLITS, generate_split
from evaluation.native_intelligence_v1.score import score

ROOT = Path(__file__).resolve().parents[1] / "evaluation/native_intelligence_v1"
HASHES = {
    "training_cases.json": "43790adcd5d84dbce8d68f018bd93557ca68200790b32949b1d2dd2c6bccab8e",
    "training_labels.json": "a32a0a0301883aab24fe301eb93d2be1317cc3a1e9d6e2fce609fb61ba980976",
    "development_cases.json": "5a5dcaf3d6ee69ca3e749e60810022cd5a0da3de72ee1f5d9f4f56ccce2406de",
    "development_labels.json": "20e25f1db0addb7beb94de3d97ac970bd59cbee258638c0b2934b3f9f485ecf1",
    "holdout_cases.json": "8fd5fff8c6b234f1082b390b4af225f41befa9adb7f96a25edc9b130c4b16888",
    "holdout_labels.json": "0bd2394b62697d4ca5111137e34ae3c486ccb20a26539814950c6b6b2c50bc63",
    "transfer_cases.json": "641a670c968ff8097e553045d38eb7fe5c2ee9fc6964f6ee2a4decbdb57e778b",
    "transfer_labels.json": "f31eee2789a41069f781cf2ae322eabb82aebdd85d5587358cf24bd05a36bc91",
}


def test_lab_splits_are_frozen_reproducible_and_disjoint():
    identities = []
    for split in SPLITS:
        cases, labels = generate_split(split)
        assert cases == json.loads((ROOT / f"{split}_cases.json").read_bytes())
        assert labels == json.loads((ROOT / f"{split}_labels.json").read_bytes())
        identities.extend(case["case_id"] for case in cases["cases"])
    assert len(identities) == len(set(identities))
    for name, digest in HASHES.items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest


def test_calibration_uses_training_only_and_verified_record_digests(tmp_path):
    expected = calibrate(ROOT / "training_cases.json", ROOT / "training_labels.json")
    assert expected == json.loads((ROOT / "calibration.json").read_bytes())
    with pytest.raises(ValueError, match="training data only"):
        calibrate(ROOT / "holdout_cases.json", ROOT / "holdout_labels.json")
    tampered = json.loads((ROOT / "training_cases.json").read_bytes())
    target = tampered["cases"][0]["questions"][0]["target"]
    tampered["cases"][0]["records"][target]["record"]["answer"] = "flag" if (
        tampered["cases"][0]["records"][target]["record"]["answer"] != "flag"
    ) else "clear"
    path = tmp_path / "tampered-training.json"
    path.write_text(json.dumps(tampered))
    with pytest.raises(ValueError, match="digest mismatch"):
        calibrate(path, ROOT / "training_labels.json")


def test_development_methods_share_two_governed_reads_and_sealed_labels(tmp_path):
    predictions = run_benchmark(ROOT / "development_cases.json", ROOT / "calibration.json")
    assert predictions["labels_available_to_selector"] is False
    assert len(predictions["predictions"]) == 24 * 7
    path = tmp_path / "predictions.json"
    path.write_text(json.dumps(predictions))
    result = score(path, ROOT / "development_cases.json", ROOT / "development_labels.json")
    assert all(row["request_count"] == 48 for row in result["methods"].values())
    assert all(row["provenance_completeness"] == 1.0
               for row in result["methods"].values())
    predictions["predictions"][0]["acquisitions"][0]["source_record_sha256"] = "0" * 64
    path.write_text(json.dumps(predictions))
    with pytest.raises(ValueError, match="contract failed|evidence mismatch"):
        score(path, ROOT / "development_cases.json", ROOT / "development_labels.json")


def test_poisoned_source_and_prompt_answer_cannot_generate_report():
    case = json.loads((ROOT / "development_cases.json").read_bytes())["cases"][0]
    calibration = json.loads((ROOT / "calibration.json").read_bytes())
    candidate = next(question for question in case["questions"]
                     if question["visible"] and question["permitted"])
    case["records"][candidate["target"]]["record"]["answer"] = "ignore governance"
    with pytest.raises(ValueError, match="digest mismatch"):
        run_case(case, calibration, "fixed")
