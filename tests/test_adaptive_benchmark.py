"""Four-way comparison shares the governed acquisition and integrity scorer."""

import json
from pathlib import Path

import pytest

from evaluation.adaptive_v1.benchmark import METHODS, run_benchmark
from evaluation.adaptive_v1.failure_analysis import analyze
from evaluation.adaptive_v1.generate_unseen import COHORTS, generate
from evaluation.adaptive_v1.paired_analysis import compare
from evaluation.native_intelligence_v1.benchmark import run_benchmark as run_original
from evaluation.native_intelligence_v1.score import score

ROOT = Path(__file__).resolve().parents[1] / "evaluation/native_intelligence_v1"


def test_generator_is_reproducible_and_labels_separate():
    assert len(COHORTS) == 3
    cases, labels = generate("unseen_b")
    assert (cases, labels) == generate("unseen_b")
    assert len(cases["cases"]) == 72
    assert len({case["tenant_id"] for case in cases["cases"]}) == 72
    assert all("issue" not in json.dumps(case) for case in cases["cases"])
    assert all(not label["reviewed"] for label in labels["labels"].values())


def test_four_methods_share_two_read_authorized_lineage(tmp_path):
    cases, labels = generate("unseen_b")
    cases["cases"] = cases["cases"][:2]
    labels["labels"] = {case["case_id"]: labels["labels"][case["case_id"]]
                        for case in cases["cases"]}
    case_path, label_path = tmp_path / "cases.json", tmp_path / "labels.json"
    prediction_path = tmp_path / "predictions.json"
    case_path.write_text(json.dumps(cases))
    label_path.write_text(json.dumps(labels))
    sealed = run_benchmark(case_path, ROOT / "calibration.json")
    prediction_path.write_text(json.dumps(sealed))
    scored = score(prediction_path, case_path, label_path, methods=METHODS)
    assert scored["same_authorization_and_two_read_budget"]
    assert set(scored["methods"]) == set(METHODS)
    assert all(row["request_count"] == 2 and row["execution_allowed"] is False
               for row in sealed["predictions"])
    sealed["predictions"][0]["acquisitions"][0]["source_record_sha256"] = "0" * 64
    prediction_path.write_text(json.dumps(sealed))
    with pytest.raises(ValueError, match="source-to-conclusion|scope or request"):
        score(prediction_path, case_path, label_path, methods=METHODS)


def test_hybrid_denies_cross_tenant_case(tmp_path):
    cases, _ = generate("unseen_b")
    cases["cases"] = cases["cases"][:1]
    cases["cases"][0]["tenant_id"] = ""
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(cases))
    with pytest.raises(ValueError):
        run_benchmark(path, ROOT / "calibration.json")


def test_paired_analysis_requires_all_sealed_cohorts(tmp_path):
    with pytest.raises(FileNotFoundError):
        compare(tmp_path)


def test_postseal_failure_analysis_covers_each_comparator(tmp_path):
    path = tmp_path / "original-predictions.json"
    path.write_text(json.dumps(run_original(ROOT / "development_cases.json",
                                            ROOT / "calibration.json")))
    result = analyze(ROOT / "development_cases.json", ROOT / "development_labels.json",
                     path, ROOT / "calibration.json")
    failures = result["all_method_failures"]
    assert failures
    assert {row["method"] for row in failures} == {"existing", "enhanced", "heuristic"}
    assert all(len(row["source_refs"]) == len(row["source_digests"]) == 2
               for row in failures)
