"""Adversarial checks for the fixed private-network HTTPS broker laboratory."""

import importlib.util
import json
import os
from pathlib import Path

import confined_https_broker_lab as lab
import isolation_lab
import pytest


def test_unavailable_kernel_never_starts_confined_source(monkeypatch):
    monkeypatch.setattr(lab, "run_lab", lambda: {"status": "BLOCKED", "reason": "unavailable"})
    monkeypatch.setattr(lab, "run_case", lambda *args, **kwargs: pytest.fail("source started"))
    report = lab.run()
    assert report["status"] == "BLOCKED" and report["cases"] == []
    assert not report["live_ready"] and not report["execution_allowed"]


def test_root_owner_is_not_certified(monkeypatch):
    monkeypatch.setattr(lab, "run_lab", lambda: {"status": "PASS"})
    monkeypatch.setattr(lab.os, "getuid", lambda: 0)
    monkeypatch.setattr(lab, "run_case", lambda *args, **kwargs: pytest.fail("source started"))
    assert lab.run()["reason"] == "unprivileged_owner_required"


@pytest.mark.parametrize("mutation", ["missing", "false", "integer", "extra"])
def test_broker_witness_cannot_be_omitted_or_forged(mutation):
    checks = dict.fromkeys(isolation_lab.CHECKS + lab.BROKER_EXTRA_CHECKS, True)
    assert lab.broker_checks_valid(checks)
    if mutation == "missing":
        del checks["approved_destination_bound"]
    elif mutation == "false":
        checks["test_network_denied"] = False
    elif mutation == "integer":
        checks["loopback_only"] = 1
    else:
        checks["host_network_shared"] = True
    assert not lab.broker_checks_valid(checks)


def test_fixed_command_has_private_network_and_only_journal_state_writable(monkeypatch, tmp_path):
    monkeypatch.setattr(lab.os, "getuid", lambda: 1000)
    monkeypatch.setattr(lab.os, "getgid", lambda: 1000)
    monkeypatch.setattr(isolation_lab.shutil, "which", lambda name: "/usr/bin/bwrap")
    monkeypatch.setattr(isolation_lab.sys, "executable", "/opt/python/bin/python")
    monkeypatch.setattr(isolation_lab.sys, "base_prefix", "/opt/python")
    paths = {
        name: tmp_path / name for name in ("config", "source", "route", "certificate", "tls_key")
    }
    command = lab.confined_command(paths, tmp_path / "state")
    assert "--unshare-all" in command and "--share-net" not in command
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert "--clearenv" in command and command.count("--bind") == 1
    assert command[command.index("--bind") + 2] == "/state"
    for path in paths.values():
        index = command.index(str(path))
        assert command[index - 1] == "--ro-bind"
    assert not any("BROKER_" in part or "proxy" in part.lower() for part in command)


def test_predecessor_command_shape_is_independent_of_runner_bwrap(monkeypatch, tmp_path):
    import broker_namespace_lab

    monkeypatch.setattr(broker_namespace_lab.os, "getuid", lambda: 1000)
    monkeypatch.setattr(broker_namespace_lab.os, "getgid", lambda: 1000)
    monkeypatch.setattr(isolation_lab.shutil, "which", lambda name: "/usr/bin/bwrap")
    monkeypatch.setattr(isolation_lab.sys, "executable", "/opt/python/bin/python")
    monkeypatch.setattr(isolation_lab.sys, "base_prefix", "/opt/python")
    command = broker_namespace_lab.broker_command(
        tmp_path / "config", tmp_path / "source", tmp_path / "state"
    )
    assert "--unshare-all" in command and "--share-net" not in command


def test_actual_confined_https_profile_or_explicit_blocker():
    report = lab.run()
    assert report["status"] in ("PASS", "BLOCKED"), report
    assert report["production_containment"] == "NOT PROVEN"
    assert not report["live_ready"] and not report["execution_allowed"]
    if report["status"] == "PASS":
        assert len(report["cases"]) == 3
        assert {case["durable_control"] for case in report["cases"]} == {"revoke", "stop"}
        for case in report["cases"]:
            assert case["status"] == "PASS" and case["owner_negative_controls"]
            assert case["pre_io_denials"] == 10
            assert case["attempts_before_acquisition"] == 0
            assert case["source_requests_before_acquisition"] == 0
            assert case["source_requests"] == 1 and case["restart_source_requests"] == 0
            assert case["budget_after_restart"]["attempts"] == 1
            assert all(case["broker_checks"].values())
            if case["behavior"] == "ok":
                assert case["observations"] == 1 and case["provenance_intact"]
                assert case["reasoner_credentials_inaccessible"] is True
                assert all(case["reasoner_checks"].values())
                assert case["reasoner_result"]["canonical_immutable"]
            else:
                assert case["observations"] == 0


@pytest.mark.parametrize(
    "output",
    [
        "[]",
        "null",
        '{"status":"PASS","live_ready":true}',
        ('{"status":"PASS","production_containment":"NOT PROVEN",'
         '"execution_allowed":false,"allow_live_customer_access":false,"live_ready":false}'),
        ('{"status":"PASS","production_containment":"NOT PROVEN",'
         '"execution_allowed":false,"allow_live_customer_access":false,"live_ready":false,'
         '"negative_controls":true,"cases":[{"protocol":[]}]}'),
        "not-json",
    ],
)
def test_operator_probe_rejects_malformed_or_overclaiming_output(monkeypatch, capsys, output):
    path = Path(__file__).resolve().parents[1] / "tools" / "confined_https_broker_probe.py"
    spec = importlib.util.spec_from_file_location("confined_https_probe_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: FakeProcess(output))
    assert module.run() == 2
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "BLOCKED" and report["execution_allowed"] is False


class FakeProcess:
    def __init__(self, output):
        self.output = output
        self.returncode = 0
        self.pid = os.getpid()

    def communicate(self, timeout=None):
        return self.output, ""

    def poll(self):
        return self.returncode
