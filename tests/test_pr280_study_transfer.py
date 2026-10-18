"""Frozen transfer generation and source-linked selection diagnostics."""

import hashlib
import json
from pathlib import Path

import pytest

from evaluation.pr279_study_selection.benchmark import run_benchmark
from evaluation.pr280_study_transfer.analyze import analyze
from evaluation.pr280_study_transfer.generate import generate

ROOT = Path(__file__).resolve().parents[1] / "evaluation"
OLD = ROOT / "pr279_study_selection"
NEW = ROOT / "pr280_study_transfer"


def test_original_cases_and_labels_remain_frozen():
    assert hashlib.sha256((OLD / "cases.json").read_bytes()).hexdigest() == (
        "8689d66882147b5a0d6bc4dbcf6dc25899ed41c6d271502818dac9d3411ab80b"
    )
    assert hashlib.sha256((OLD / "labels.json").read_bytes()).hexdigest() == (
        "17336b28143e2921c69bbe5ab9cea9c8fc77cd6a507e97916b55831e1683db7a"
    )


def test_transfer_fixture_is_independently_reproducible():
    cases, labels = generate()
    assert cases == json.loads((NEW / "cases.json").read_bytes())
    assert labels == json.loads((NEW / "labels.json").read_bytes())
    assert hashlib.sha256((NEW / "cases.json").read_bytes()).hexdigest() == (
        "ab8f496911aadd9d5c7558897172e8edbb21eed174959a36891e9798a711fc35"
    )
    assert hashlib.sha256((NEW / "labels.json").read_bytes()).hexdigest() == (
        "3b281034bb8039d3fc7a36396c00252ff2a58b5f3fd4ec1927e2a82b5ec4c62f"
    )


def test_transfer_evaluation_uses_equal_governed_reads_and_source_digests(tmp_path):
    predictions = run_benchmark(NEW / "cases.json")
    assert predictions["labels_available_to_selector"] is False
    assert len(predictions["predictions"]) == 32 * 4
    path = tmp_path / "sealed.json"
    path.write_text(json.dumps(predictions))
    result = analyze(NEW / "cases.json", NEW / "labels.json", path)
    assert all(method["evidence_requests"] == 64
               for method in result["methods"].values())
    assert result["methods"]["orion"]["structural_edges_resolved"] > (
        result["methods"]["fixed"]["structural_edges_resolved"]
    )
    assert result["methods"]["orion"]["fp"] == 0
    predictions["predictions"][0]["conclusion"]["record_sha256"] = "0" * 64
    path.write_text(json.dumps(predictions))
    with pytest.raises(ValueError, match="source-to-conclusion"):
        analyze(NEW / "cases.json", NEW / "labels.json", path)
    predictions["predictions"][0]["conclusion"]["record_sha256"] = result[
        "methods"
    ]["orion"]["cases"][0]["source_sha256"]
    predictions["predictions"][0]["structural_sha256"] = "0" * 64
    path.write_text(json.dumps(predictions))
    with pytest.raises(ValueError, match="source-to-conclusion"):
        analyze(NEW / "cases.json", NEW / "labels.json", path)
