"""Synthetic trusted signatures prove that a release cannot self-approve."""

import base64
import hashlib
import json
import subprocess
import tomllib
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from scripts.verify_release_gate import REQUIRED_CHECKS, REQUIRED_DISTS, ReleaseDenied, verify


def test_required_distribution_names_match_package_metadata():
    root = Path(__file__).resolve().parents[1]
    projects = (
        root / "pyproject.toml",
        root / "packages/connector-sdk/pyproject.toml",
        root / "packages/erpnext-discovery/pyproject.toml",
        root / "packages/synthetic-connector/pyproject.toml",
        root / "packages/pilot-compat/pyproject.toml",
    )
    actual = {
        tomllib.loads(path.read_text())["project"]["name"].replace("-", "_")
        for path in projects
    }
    assert actual == REQUIRED_DISTS


def _json(path, value):
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")))


def _fixture(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("synthetic source checkout\n")
    for command in (
        ("init", "-q"),
        ("config", "user.email", "synthetic@example.invalid"),
        ("config", "user.name", "Synthetic Builder"),
        ("add", "README.md"),
        ("commit", "-qm", "synthetic source"),
    ):
        subprocess.run(["git", "-C", str(source), *command], check=True)
    sha = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    tree = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD^{tree}"], text=True
    ).strip()
    artifacts = tmp_path / "artifacts"
    approvals = tmp_path / "approvals"
    records = tmp_path / "records"
    artifacts.mkdir()
    approvals.mkdir()
    records.mkdir()
    inventory = {}
    for distribution in REQUIRED_DISTS:
        for name in (
            f"{distribution}-0.2.0rc1-py3-none-any.whl",
            f"{distribution}-0.2.0rc1.tar.gz",
        ):
            artifact = artifacts / name
            artifact.write_bytes(f"synthetic reviewed {name}".encode())
            inventory[name] = hashlib.sha256(artifact.read_bytes()).hexdigest()
    record_hashes = {}
    for name in REQUIRED_CHECKS | {"known_risks"}:
        path = records / f"{name}.json"
        _json(path, {"synthetic_evidence": name})
        record_hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {
        "schema": "orion.release-candidate.v1",
        "source_sha": sha,
        "tree": tree,
        "source_author": "Synthetic Builder <synthetic@example.invalid>",
        "version": "0.2.0rc1",
        "component_versions": dict.fromkeys(REQUIRED_DISTS, "0.2.0rc1"),
        "checks": dict.fromkeys(REQUIRED_CHECKS, "PASS"),
        "artifacts": inventory,
        "records": record_hashes,
    }
    manifest_path = tmp_path / "manifest.json"
    _json(manifest_path, manifest)
    digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    trust = {"keys": {}}
    for role in ("security", "architecture", "release_owner"):
        key = Ed25519PrivateKey.generate()
        identity = f"independent-{role}"
        key_id = f"synthetic-{role}"
        trust["keys"][key_id] = {
            "role": role,
            "reviewer_identity": identity,
            "public_key": base64.b64encode(
                key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
            ).decode(),
        }
        signed = {
            "decision": "APPROVE",
            "key_id": key_id,
            "manifest_sha256": digest,
            "reviewer_identity": identity,
            "role": role,
        }
        signature = key.sign(
            json.dumps(signed, sort_keys=True, separators=(",", ":")).encode()
        )
        _json(
            approvals / f"{role}.json",
            {**signed, "signature": base64.b64encode(signature).decode()},
        )
    trust_path = tmp_path / "trust.json"
    _json(trust_path, trust)
    return manifest_path, artifacts, trust_path, approvals, source, records


def test_three_independent_signatures_and_exact_artifact_pass(tmp_path):
    result = verify(*_fixture(tmp_path))
    assert result["release_gate"] == "PASS"
    assert result["artifact_count"] == 10


def test_missing_or_self_issued_approval_denied(tmp_path):
    paths = _fixture(tmp_path)
    (paths[3] / "security.json").unlink()
    with pytest.raises(ReleaseDenied):
        verify(*paths)
    second = tmp_path / "second"
    second.mkdir()
    paths = _fixture(second)
    approval_path = paths[3] / "release_owner.json"
    approval = json.loads(approval_path.read_text())
    approval["reviewer_identity"] = "Synthetic Builder <synthetic@example.invalid>"
    _json(approval_path, approval)
    with pytest.raises(ReleaseDenied, match="non-independent approval"):
        verify(*paths)


def test_artifact_tamper_and_failed_check_denied(tmp_path):
    paths = _fixture(tmp_path)
    next(paths[1].iterdir()).write_bytes(b"substituted package")
    with pytest.raises(ReleaseDenied, match="artifact hash mismatch"):
        verify(*paths)
    third = tmp_path / "third"
    third.mkdir()
    paths = _fixture(third)
    manifest = json.loads(paths[0].read_text())
    manifest["checks"]["security_review"] = "FAIL"
    _json(paths[0], manifest)
    with pytest.raises(ReleaseDenied, match="mandatory release check"):
        verify(*paths)


def test_approval_must_bind_exact_manifest_and_trusted_key(tmp_path):
    paths = _fixture(tmp_path)
    approval_path = paths[3] / "architecture.json"
    approval = json.loads(approval_path.read_text())
    approval["manifest_sha256"] = "0" * 64
    _json(approval_path, approval)
    with pytest.raises(ReleaseDenied, match="non-independent approval"):
        verify(*paths)


def test_dirty_or_wrong_source_checkout_denied(tmp_path):
    paths = _fixture(tmp_path)
    (paths[4] / "extra.py").write_text("untracked source")
    with pytest.raises(ReleaseDenied, match="source checkout"):
        verify(*paths)


def test_manifest_cannot_invent_source_author_to_enable_self_review(tmp_path):
    paths = _fixture(tmp_path)
    manifest = json.loads(paths[0].read_text())
    manifest["source_author"] = "a different builder"
    _json(paths[0], manifest)
    with pytest.raises(ReleaseDenied, match="source author"):
        verify(*paths)


def test_signature_or_evidence_record_tamper_denied(tmp_path):
    paths = _fixture(tmp_path)
    approval_path = paths[3] / "security.json"
    approval = json.loads(approval_path.read_text())
    approval["signature"] = base64.b64encode(b"invalid signature").decode()
    _json(approval_path, approval)
    with pytest.raises(ReleaseDenied, match="signature invalid"):
        verify(*paths)
    second = tmp_path / "record-tamper"
    second.mkdir()
    paths = _fixture(second)
    (paths[5] / "rollback.json").write_text('{"synthetic_evidence":"altered"}')
    with pytest.raises(ReleaseDenied, match="record hash mismatch"):
        verify(*paths)


def test_mixed_component_version_must_match_exact_artifacts(tmp_path):
    paths = _fixture(tmp_path)
    manifest = json.loads(paths[0].read_text())
    erp = "originorion_connector_erpnext_discovery"
    manifest["component_versions"][erp] = "0.2.0rc2"
    _json(paths[0], manifest)
    with pytest.raises(ReleaseDenied, match="required wheel"):
        verify(*paths)


def test_missing_or_extra_component_version_denied(tmp_path):
    paths = _fixture(tmp_path)
    manifest = json.loads(paths[0].read_text())
    manifest["component_versions"].pop("originorion_connector_sdk")
    _json(paths[0], manifest)
    with pytest.raises(ReleaseDenied, match="component version inventory"):
        verify(*paths)
