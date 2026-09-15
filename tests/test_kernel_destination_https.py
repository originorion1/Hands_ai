"""Kernel proof cannot be replaced by absent listeners or application validation."""

import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import destination_network_lab as network
import isolation_lab
import kernel_destination_https_lab as lab
import pytest
from https_broker_support import HTTPSBroker, LocalPolicy, certificates
from test_supervised_broker import Harness

from orion.pilot.broker_contract import authenticate, digest
from orion.understanding.role_checkpoint import _json

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
spec = importlib.util.spec_from_file_location(
    "destination_probe_test", TOOLS / "kernel_destination_https_probe.py"
)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def case(protocol, behavior, control, ipv6):
    host = network.V6_APPROVED if ipv6 and protocol == "local_columns_v1" else network.V4_APPROVED
    names = {item[0] for item in network.targets(ipv6, host)}
    value = {
        "status": "PASS",
        "protocol": protocol,
        "behavior": behavior,
        "durable_control": control,
        "host": host,
        "ipv6_enabled": ipv6,
        "source_outside_broker": True,
        "broker_checks": dict.fromkeys(probe.BROKER_CHECKS, True),
        "reachable_controls": dict.fromkeys(
            names | {"approved-ipv4"} | ({"approved-ipv6"} if ipv6 else set()), True
        ),
        "kernel_direct_denials": [
            [
                {
                    "direct": name,
                    "connected": False,
                    "errno": None,
                    "counter": network.counter_for(name),
                    "kernel_rejections": 1,
                }
                for name in sorted(names)
            ]
            for _ in range(3)
        ],
        "pre_io_denials": 10,
        "source_requests_before_acquisition": 0,
        "source_requests": 1,
        "approved_acquisition_packets": 12,
        "unauthorized_approved_packets": 0,
        "restart_approved_packets": 0,
        "restart_source_requests": 0,
        "unapproved_connections_after_controls": 0,
        "budget_restart_exhaustion_denied": True,
        "budget_after_restart": {
            "attempts": 1,
            "failures": int(behavior == "redirect"),
            "reserved_bytes": 65536,
        },
        "observations": int(behavior == "ok"),
        "provenance_intact": True,
        "execution_allowed": False,
        "live_ready": False,
        "reasoner_checks": dict.fromkeys(probe.REASONER_CHECKS, True),
        "reasoner_result": {"canonical_immutable": True, "execution_allowed": False},
    }
    return value


def report(ipv6=True):
    value = isolation_lab.result(
        "PASS", "kernel_single_synthetic_https_destination", negative_controls=True
    )
    value["rootless_setup"] = True
    value["cases"] = [
        case(protocol, behavior, control, ipv6)
        for protocol, behavior, control in (
            ("local_rows_v1", "ok", "revoke"),
            ("local_columns_v1", "ok", "stop"),
            ("local_rows_v1", "redirect", "revoke"),
            ("local_columns_v1", "redirect", "stop"),
        )
    ]
    return value


@pytest.mark.parametrize("ipv6", [True, False])
def test_complete_witness_matrix_is_required(ipv6):
    assert probe.valid_report(report(ipv6))


@pytest.mark.parametrize(
    "mutation",
    [
        "absent-listener",
        "application-only",
        "no-counter",
        "generic-counter",
        "connected",
        "missing-ipv6",
        "missing-mapped",
        "proxy-unreachable",
        "source-inside",
        "capability",
        "restart",
        "budget",
        "credential",
        "provenance",
        "ready",
    ],
)
def test_missing_boundary_witness_never_passes(mutation):
    value = copy.deepcopy(report())
    item = value["cases"][0]
    if mutation == "absent-listener":
        item["reachable_controls"]["ipv4-unapproved"] = False
    elif mutation == "application-only":
        item["kernel_direct_denials"] = []
    elif mutation == "no-counter":
        item["kernel_direct_denials"][0][0]["kernel_rejections"] = 0
    elif mutation == "generic-counter":
        item["kernel_direct_denials"][0][0]["counter"] = "denied"
    elif mutation == "connected":
        item["kernel_direct_denials"][0][0]["connected"] = True
    elif mutation in ("missing-ipv6", "missing-mapped"):
        name = "ipv6-unapproved" if mutation == "missing-ipv6" else "ipv4-mapped-ipv6"
        item["kernel_direct_denials"][0] = [
            w for w in item["kernel_direct_denials"][0] if w["direct"] != name
        ]
    elif mutation == "proxy-unreachable":
        del item["reachable_controls"]["ipv4-proxy"]
    elif mutation == "source-inside":
        item["source_outside_broker"] = False
    elif mutation == "capability":
        item["broker_checks"]["firewall_mutation_denied"] = False
    elif mutation == "restart":
        item["kernel_direct_denials"].pop()
    elif mutation == "budget":
        item["budget_after_restart"]["attempts"] = 0
    elif mutation == "credential":
        item["reasoner_checks"]["tls_private_key_denied"] = False
    elif mutation == "provenance":
        item["provenance_intact"] = False
    else:
        value["live_ready"] = True
    assert not probe.valid_report(value)


def test_missing_tools_never_starts_private_setup(monkeypatch):
    monkeypatch.setattr(lab, "prerequisites", lambda: False)
    monkeypatch.setattr(lab.subprocess, "run", lambda *a, **k: pytest.fail("setup attempted"))
    value = lab.run()
    assert value["status"] == "BLOCKED" and value["cases"] == []
    assert not value["live_ready"]


def test_kernel_operation_failure_is_blocked_without_fallback(monkeypatch):
    monkeypatch.setattr(
        network.subprocess,
        "run",
        lambda *a, **k: network.subprocess.CompletedProcess(a, 1, "", "unavailable"),
    )
    with pytest.raises(network.KernelUnavailable):
        network.checked(["nft", "list", "tables"])


def test_jail_inherits_only_preconfigured_private_network_and_drops_capabilities(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(isolation_lab.shutil, "which", lambda *a, **k: "/usr/bin/bwrap")
    monkeypatch.setattr(isolation_lab.sys, "executable", "/opt/python/bin/python")
    monkeypatch.setattr(isolation_lab.sys, "base_prefix", "/opt/python")
    paths = {
        name: tmp_path / name for name in ("config", "source", "route", "certificate", "tls_key")
    }
    broker = lab.command(paths, state=tmp_path / "state")
    source = lab.command(paths, source=True)
    for command in (broker, source):
        assert "--share-net" not in command and "--unshare-net" not in command
        assert "--unshare-user" in command and "--unshare-pid" in command
        assert command[command.index("--cap-drop") + 1] == "ALL" and "--clearenv" in command
    assert str(paths["source"]) not in broker and str(paths["tls_key"]) not in broker
    assert broker.count("--bind") == 1 and source.count("--bind") == 0


@pytest.mark.parametrize("host,ipv6", [(network.V4_APPROVED, False), (network.V6_APPROVED, True)])
def test_kernel_rules_have_one_allow_tuple_and_unconditional_default_deny(host, ipv6):
    rules = network.firewall(host, ipv6=ipv6)
    assert rules.count(" accept") == 1 and "policy drop" in rules
    assert f"daddr {host} tcp dport 44443 counter name approved accept" in rules
    assert "counter name denied reject" in rules
    assert "ct state" not in rules and "lo" not in rules and "udp" not in rules
    with pytest.raises(ValueError):
        network.firewall("203.0.113.99")


def test_signed_host_tamper_denied_before_network(tmp_path, monkeypatch):
    from confined_https_broker_lab import prepare

    h = Harness(tmp_path / "broker")
    paths = prepare(h, host=network.V4_APPROVED)
    monkeypatch.setenv("BROKER_AUTH_KEY", h.key.decode())
    monkeypatch.setenv("BROKER_SOURCE_SECRET", h.secret)
    envelope = json.loads(paths["route"].read_text())
    envelope["profile"]["host"] = network.V4_UNAPPROVED
    paths["route"].write_text(_json(envelope))
    h.config["source_path"] = str(h.source)
    # Bind the initial profile to the real owner config, then tamper ONLY host.
    envelope["profile"]["binding"] = digest(h.config)
    original = dict(envelope["profile"], host=network.V4_APPROVED)
    envelope["mac"] = authenticate(h.key, "local_https_fixture", original)
    paths["route"].write_text(_json(envelope))
    with pytest.raises(ValueError, match="route denied"):
        HTTPSBroker(h.config, h.state)


@pytest.mark.parametrize("host", [network.V4_APPROVED, network.V6_APPROVED])
def test_numeric_fixture_certificate_verifies_exact_ip(host, tmp_path):
    cert, key = certificates(tmp_path, hostname=host)
    policy = LocalPolicy(44443, str(cert), hashlib.sha256(cert.read_bytes()).hexdigest(), host)
    from test_local_https_broker import tls_pair

    tls_pair(cert, key, policy.context(), host)
    policy.authorize_wire(host=host, port=44443, method="GET", target="/fixture", body=None)
    with pytest.raises(ValueError):
        policy.authorize_wire(
            host=network.V4_UNAPPROVED, port=44443, method="GET", target="/fixture", body=None
        )


def test_actual_kernel_destination_lab_or_explicit_blocker():
    value = lab.run()
    assert value["status"] in ("PASS", "BLOCKED"), value
    assert probe.valid_report(value)


@pytest.mark.parametrize(
    "value", [None, [], {"status": "PASS"}, {"status": "PASS", "live_ready": True}]
)
def test_malformed_operator_output_is_blocked(monkeypatch, capsys, value):
    import confined_https_broker_probe as launcher

    class Process:
        returncode = 0

        def communicate(self, timeout):
            return json.dumps(value), ""

        def poll(self):
            return 0

    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *a, **k: Process())
    assert probe.run() == 2
    assert json.loads(capsys.readouterr().out)["status"] == "BLOCKED"


@pytest.mark.parametrize("host", [True, "source.invalid", "fe80::1%lo"])
def test_nonnumeric_or_scoped_host_rejected_before_certificate_io(host):
    policy = LocalPolicy(44443, "/absent-certificate", "0" * 64, host)
    with pytest.raises(ValueError):
        policy.context()
