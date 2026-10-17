"""A fresh loopback process recovers synthetic report custody after failure."""

import json
from hashlib import sha256
from pathlib import Path

import pytest

from deployment.release_candidate.report_drill import run
from orion_http import report_server


def test_installed_style_report_process_recovers_without_execution(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "source_sha": "a" * 40,
        "tree": "b" * 40,
        "version": "0.2.0rc1",
        "report_server_module_sha256": sha256(Path(report_server.__file__).read_bytes()).hexdigest(),
    }))
    result = run(manifest)
    assert result["tests"] == "PASS"
    assert result["execution_allowed"] is False
    assert result["observations"] == 4
    assert result["data_loss_bytes"] == 0
    assert result["graceful_shutdown_ms"] >= 0


def test_different_installed_report_module_is_denied(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "source_sha": "a" * 40,
        "tree": "b" * 40,
        "version": "0.2.0rc1",
        "report_server_module_sha256": "0" * 64,
    }))
    with pytest.raises(RuntimeError, match="installed release identity"):
        run(manifest)
