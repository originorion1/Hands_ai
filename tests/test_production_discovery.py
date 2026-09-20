"""Governed production-discovery contract without customer connectivity."""

import copy
import hashlib
import io
import os
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_grant_transition import transition_inputs

from orion.contracts import utc_now
from orion.pilot import deployment, production, readiness
from orion.pilot.broker_contract import (
    PRODUCTION_GRANT_TRANSITION_VERSION,
    authenticate,
    digest,
)
from orion.pilot.deployment_profile import profile_for_manifest, profile_sha256
from orion.pilot.gateway import CredentialGateway
from orion.pilot.isolation import reviewed_firewall
from orion.pilot.progress_witness import deployment_identity_for_manifest
from orion.understanding.role_checkpoint import _json

ISSUER = b"synthetic-production-issuer-key-000000000"
DEPLOYMENT_APPROVAL = b"synthetic-deployment-approval-key-00000"


def approved(body, purpose, key, now):
    payload = {
        "version": production.APPROVAL_VERSION,
        "purpose": purpose,
        "subject_sha256": digest(body),
        "controller_id": "synthetic-independent-controller",
        "issued_at": (now - timedelta(minutes=1)).isoformat(),
        "expires_at": body["expires_at"],
    }
    return {**payload, "mac": authenticate(key, purpose, payload)}


def private_json(path, value):
    path.write_text(_json(value))
    path.chmod(0o600)
    return str(path)


def inputs(tmp_path, *, network_mode="local_qualification", now=None):
    now = now or utc_now()
    origin = (
        "https://opaque.test:44443"
        if network_mode == "local_qualification"
        else "https://erp.example.com:443"
    )
    addresses = ["192.0.2.2"] if network_mode == "local_qualification" else ["10.20.30.40"]
    metadata, _, policy = transition_inputs()
    metadata = copy.deepcopy(metadata)
    policy = copy.deepcopy(policy)
    policy["version"] = PRODUCTION_GRANT_TRANSITION_VERSION
    metadata["grant"]["request"]["source_id"] = origin
    policy["source_id"] = origin
    certificate = tmp_path / "certificate.pem"
    certificate.write_bytes(b"synthetic reviewed certificate")
    certificate.chmod(0o600)
    certificate_sha256 = hashlib.sha256(certificate.read_bytes()).hexdigest()
    canonical_expires = metadata["limits"]["expires_at"]
    destination = {
        "version": production.DESTINATION_VERSION,
        "network_mode": network_mode,
        "origin": origin,
        "hostname": "opaque.test" if network_mode == "local_qualification" else "erp.example.com",
        "port": 44443 if network_mode == "local_qualification" else 443,
        "addresses": addresses,
        "tls_server_name": (
            "opaque.test" if network_mode == "local_qualification" else "erp.example.com"
        ),
        "certificate_sha256": certificate_sha256,
        "resolution_observed_at": (now - timedelta(minutes=2)).isoformat(),
        "resolution_expires_at": canonical_expires,
    }
    root, keys, witness = tmp_path / "state", tmp_path / "keys", tmp_path / "witness"
    for directory in (root, keys, witness):
        directory.mkdir(mode=0o700)
    for name, value in {
        "issuer": ISSUER,
        "deployment-approval": DEPLOYMENT_APPROVAL,
    }.items():
        path = keys / name
        path.write_bytes(value)
        path.chmod(0o600)
    manifest_path = tmp_path / "manifest.json"
    manifest = {
        "version": 6,
        "mode": "governed_erpnext_discovery_read_only",
        "configs": [metadata],
        "state_directory": str(root),
        "keys_directory": str(keys),
        "witness_directory": str(witness),
        "certificate": str(certificate),
        "certificate_sha256": certificate_sha256,
        "artifact_record_sha256": "a" * 64,
        "host": addresses[0],
        "policy": {"max_entries": 20, "max_bytes": 262144, "ttl_seconds": 3600},
        "grant_transition": policy,
        "destination": destination,
        "access_ledger": str(tmp_path / "access-ledger.json"),
        "access_approval": str(tmp_path / "access-approval.json"),
        "host_attestation": str(tmp_path / "host-attestation.json"),
        "host_approval": str(tmp_path / "host-approval.json"),
    }
    artifact = {
        "name": "orion-core",
        "version": "0.1.0",
        "record_sha256": "a" * 64,
        "interpreter": "/opt/orion/runtime/bin/python",
        "installed_prefix": "/opt/orion/runtime",
        "package_root": "/opt/orion/runtime/lib/python3.12/site-packages/orion",
    }
    manifest["deployment_identity"] = deployment_identity_for_manifest(manifest, artifact)
    profile = profile_for_manifest(manifest, artifact, manifest_path)
    manifest["deployment_profile"] = profile
    manifest["deployment_profile_sha256"] = profile_sha256(profile)
    ledger_issued = now - timedelta(minutes=1)
    ledger_expires = datetime.fromisoformat(canonical_expires)
    ledger = {
        "version": production.LEDGER_VERSION,
        "session_id": "synthetic-new-session-193",
        "deployment_identity": manifest["deployment_identity"],
        "artifact_record_sha256": manifest["artifact_record_sha256"],
        "destination_sha256": digest(destination),
        "metadata_binding": digest(metadata),
        "transition_envelope_sha256": digest(policy),
        "tenant_id": metadata["grant"]["request"]["tenant_id"],
        "company": metadata["grant"]["request"]["company"],
        "source_id": origin,
        "credential_reference": metadata["secret_reference"],
        "issuer_reference": metadata["auth_reference"],
        "site_schema": "erpnext-read-only-v1",
        "exclusions": ["customer-writes", "recommendations", "semantic-promotion"],
        "limits": {
            "max_requests": metadata["limits"]["max_requests"],
            "response_bytes": metadata["limits"]["response_bytes"],
            "total_response_bytes": metadata["limits"]["total_response_bytes"],
            "duration_seconds": int(
                (ledger_expires - ledger_issued).total_seconds()
            )
            + 1,
        },
        "prior_consumed_sessions": ["b" * 64],
        "issued_at": ledger_issued.isoformat(),
        "expires_at": ledger_expires.isoformat(),
    }
    attestation = {
        "version": production.ATTESTATION_VERSION,
        "deployment_identity": manifest["deployment_identity"],
        "artifact_record_sha256": manifest["artifact_record_sha256"],
        "deployment_profile_sha256": manifest["deployment_profile_sha256"],
        "installed_qualification_sha256": "c" * 64,
        "destination_sha256": digest(destination),
        "access_ledger_sha256": digest(ledger),
        "network_namespace": (
            "local-qualification-generated" if network_mode == "local_qualification" else "net:[193]"
        ),
        "host_network_namespace": "net:[1]",
        "service": {
            name: profile["entrypoint"][name]
            for name in ("interpreter", "module", "manifest_path", "working_directory", "environment")
        },
        "controls": [
            "exact-address-default-deny-egress",
            "gateway-only-network-role",
            "private-owner-only-custody",
            "rootless-unprivileged-service",
            "stop-terminates-egress-role",
            "wheel-only-isolated-module",
        ],
        "observed_at": (now - timedelta(minutes=1)).isoformat(),
        "expires_at": ledger_expires.isoformat(),
    }
    private_json(Path(manifest["access_ledger"]), ledger)
    private_json(
        Path(manifest["access_approval"]),
        approved(ledger, "discovery_access_ledger", ISSUER, now),
    )
    private_json(Path(manifest["host_attestation"]), attestation)
    private_json(
        Path(manifest["host_approval"]),
        approved(attestation, "discovery_host_attestation", DEPLOYMENT_APPROVAL, now),
    )
    return manifest, artifact, ledger, attestation, now


def test_v6_profile_binds_destination_private_evidence_and_unarmed_transition(tmp_path):
    manifest, artifact, _, _, _ = inputs(tmp_path)
    profile = profile_for_manifest(manifest, artifact, tmp_path / "manifest.json")
    assert profile["version"] == 6
    assert profile["network"] == {
        "mode": "reviewed_exact_destination_egress",
        "network_mode": "local_qualification",
        "origin": "https://opaque.test:44443",
        "hostname": "opaque.test",
        "approved_addresses": ["192.0.2.2"],
        "approved_port": 44443,
        "tls_server_name": "opaque.test",
        "certificate_sha256": manifest["certificate_sha256"],
        "resolution_observed_at": manifest["destination"]["resolution_observed_at"],
        "resolution_expires_at": manifest["destination"]["resolution_expires_at"],
        "protocol": "erpnext_read_only_v1",
        "dns": "exact_reviewed_set_or_deny",
        "redirects": "deny",
        "proxies": "deny",
        "alternate_ports": "deny",
    }
    assert profile["production_discovery"]["startup"] == "unarmed_metadata_only"
    assert profile["production_discovery"]["record_access"] == (
        "separate_witnessed_transition_then_arm"
    )
    assert "deployment-approval" in profile["roles"]["authorization"]["secret_refs"]


@pytest.mark.parametrize(
    "mutation",
    (
        lambda m, l, a: l.update(source_id="https://other.example:443"),
        lambda m, l, a: l["limits"].update(max_requests=99),
        lambda m, l, a: l.update(expires_at=l["issued_at"]),
        lambda m, l, a: a.update(deployment_profile_sha256="0" * 64),
        lambda m, l, a: a["controls"].remove("gateway-only-network-role"),
    ),
)
def test_ledger_or_host_broadening_denies_without_state_dns_or_source_io(
    tmp_path, mutation, monkeypatch
):
    manifest, _, ledger, attestation, now = inputs(tmp_path)
    mutation(manifest, ledger, attestation)
    private_json(Path(manifest["access_ledger"]), ledger)
    private_json(
        Path(manifest["access_approval"]),
        approved(ledger, "discovery_access_ledger", ISSUER, now),
    )
    private_json(Path(manifest["host_attestation"]), attestation)
    private_json(
        Path(manifest["host_approval"]),
        approved(attestation, "discovery_host_attestation", DEPLOYMENT_APPROVAL, now),
    )
    calls = []
    monkeypatch.setattr(
        deployment.subprocess, "run", lambda *args, **kwargs: calls.append(args)
    )
    with pytest.raises(ValueError):
        production.load_production_evidence(manifest, now=now)
    assert calls == []
    assert not (Path(manifest["state_directory"]) / "session-forbidden").exists()


def test_missing_or_package_forged_approval_denies(tmp_path):
    manifest, _, _, _, now = inputs(tmp_path)
    Path(manifest["access_approval"]).unlink()
    with pytest.raises(FileNotFoundError):
        production.load_production_evidence(manifest, now=now)
    forged = approved(
        production.decode(production.private_bytes(manifest["access_ledger"])),
        "discovery_access_ledger",
        b"package-derived-forged-key-0000000000",
        now,
    )
    private_json(Path(manifest["access_approval"]), forged)
    with pytest.raises(ValueError, match="authentication denied"):
        production.load_production_evidence(manifest, now=now)


def test_gateway_uses_reviewed_hostname_addresses_port_and_no_dns_or_request_selector(
    tmp_path, monkeypatch
):
    manifest, _, _, _, _ = inputs(tmp_path)
    config = manifest["configs"][0]
    calls = []
    gateway = CredentialGateway(
        [config],
        lambda action, value: {"authorized": True, "binding": value["binding"]},
        "CandidateKey123456:CandidateSecret123456",
        manifest["certificate"],
        manifest["certificate_sha256"],
        manifest["host"],
        transition=manifest["grant_transition"],
        destination=manifest["destination"],
    )

    class Input(io.BytesIO):
        pass

    class Native:
        stdin = Input()
        stdout = io.BytesIO(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")

        def wait(self, timeout):
            return 0

        def poll(self):
            return 0

    monkeypatch.setattr(
        "orion.pilot.gateway.subprocess.Popen",
        lambda argv, **kwargs: calls.append((argv, kwargs)) or Native(),
    )
    assert gateway.read("/api/resource/DocType") == b"{}"
    argv, kwargs = calls[0]
    assert "--resolve" in argv
    assert argv[argv.index("--resolve") + 1] == "opaque.test:44443:192.0.2.2"
    assert argv[-1] == "https://opaque.test:44443/api/resource/DocType"
    assert "--location" not in argv and kwargs["env"] == {
        "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": "C.UTF-8",
    }


def test_reviewed_firewall_allows_only_exact_addresses_and_port():
    rules = reviewed_firewall(["10.20.30.40", "2001:db8::40"], 443)
    assert rules.count(" accept") == 2
    assert "ip daddr 10.20.30.40 tcp dport 443" in rules
    assert "ip6 daddr 2001:db8::40 tcp dport 443" in rules
    assert "policy drop" in rules and "udp" not in rules and "ct state" not in rules


def test_external_dns_drift_denies_before_deployment_state(tmp_path, monkeypatch):
    manifest, _, _, _, now = inputs(
        tmp_path, network_mode="reviewed_external"
    )
    evidence = production.load_production_evidence(manifest, now=now)
    monkeypatch.setattr(
        deployment.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout="10.20.30.41 STREAM opaque\n"
        ),
    )
    with pytest.raises(deployment.KernelUnavailable, match="resolution changed"):
        deployment.validate_external_resolution(manifest, evidence)
    assert not any(Path(manifest["state_directory"]).iterdir())


@pytest.mark.parametrize(
    "mutation",
    (
        lambda destination: destination.update(origin="https://opaque.test:44444"),
        lambda destination: destination.update(tls_server_name="other.test"),
        lambda destination: destination.update(certificate_sha256="0" * 64),
        lambda destination: destination.update(addresses=["192.0.2.3"]),
    ),
)
def test_local_qualification_destination_drift_denies(tmp_path, mutation):
    manifest, _, _, _, _ = inputs(tmp_path)
    mutation(manifest["destination"])
    with pytest.raises(ValueError):
        production.destination_from(
            manifest["destination"], manifest["certificate_sha256"]
        )


def test_scope_readiness_does_not_change_full_release_or_authorize_qualification(
    tmp_path, monkeypatch
):
    manifest, _, _, _, _ = inputs(tmp_path)
    evidence = production.load_production_evidence(manifest)
    monkeypatch.setattr(
        deployment, "load_manifest", lambda path, enrollment=False: manifest
    )
    monkeypatch.setattr(production, "load_production_evidence", lambda value: evidence)
    report = readiness.discovery_release_report("private-manifest")
    assert report["status"] == "qualification_ready"
    assert report["qualification_ready"] is True
    assert report["ready_for_unarmed_startup"] is False
    assert report["execution_allowed"] is False
    assert report["allow_live_customer_access"] is False
    assert {gate["category"]: gate["status"] for gate in report["gates"]}[
        "SEMANTIC_UNDERSTANDING"
    ] == "NOT_APPLICABLE"
    assert readiness.release_report()["live_ready"] is False


def test_reviewed_launcher_is_fixed_and_external_only(tmp_path, monkeypatch):
    inputs(tmp_path, network_mode="reviewed_external")
    captured = []
    monkeypatch.setattr(
        readiness,
        "discovery_release_report",
        lambda path: {"ready_for_unarmed_startup": True},
    )
    monkeypatch.setattr(
        readiness.os,
        "execve",
        lambda executable, argv, environment: captured.append(
            (executable, argv, environment)
        ),
    )
    with pytest.raises(RuntimeError, match="launcher returned"):
        readiness.start_discovery_pilot(tmp_path / "manifest.json")
    executable, argv, environment = captured[0]
    assert argv == [
        executable,
        "-I",
        "-m",
        "orion.pilot.deployment",
        "--serve",
        str((tmp_path / "manifest.json").resolve()),
    ]
    assert environment == {"PATH": os.defpath}
