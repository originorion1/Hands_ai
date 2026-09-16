"""Machine-readable deployment-profile contract tests."""

import copy
import hashlib
import json
import threading
from pathlib import Path

import pytest

from orion.pilot import deployment
from orion.pilot.deployment_profile import (
    profile_for_manifest,
    profile_sha256,
    validate_profile,
    validate_secret_layout,
)


def _inputs(tmp_path):
    manifest = {
        "version": 1,
        "mode": "synthetic_read_only",
        "configs": [{"operation": "metadata"}, {"operation": "read"}],
        "state_directory": str(tmp_path / "state"),
        "keys_directory": str(tmp_path / "keys"),
        "certificate": str(tmp_path / "certificate.pem"),
        "certificate_sha256": "0" * 64,
        "artifact_record_sha256": "a" * 64,
        "host": deployment.V4_APPROVED,
        "policy": {"max_entries": 20, "max_bytes": 1024, "ttl_seconds": 60},
    }
    artifact = {"name": "orion-core", "version": "0.1.0", "record_sha256": "a" * 64}
    profile = profile_for_manifest(manifest, artifact)
    manifest["deployment_profile"] = profile
    manifest["deployment_profile_sha256"] = profile_sha256(profile)
    return manifest, artifact


def test_profile_binds_artifact_roles_mounts_network_and_secret_owners(tmp_path):
    manifest, artifact = _inputs(tmp_path)
    validated = validate_profile(manifest["deployment_profile"], manifest, artifact)
    assert validated["artifact"]["record_sha256"] == manifest["artifact_record_sha256"]
    assert validated["entrypoint"] == {
        "console_script": "orion-runtime",
        "module": "orion.pilot.deployment",
        "interpreter_mode": "isolated_python",
    }
    assert validated["roles"]["gateway"]["network"] == "approved-destination-only"
    assert validated["roles"]["reasoning"]["secret_refs"] == []
    assert validated["secret_references"]["source-credential"]["owner_role"] == "gateway"
    assert validated["lifecycle"]["authority_restore"] == "forbidden"


@pytest.mark.parametrize(
    "mutation",
    (
        lambda p: p["entrypoint"].update(module="orion.legacy"),
        lambda p: p["roles"]["acquisition"].update(network="approved-destination-only"),
        lambda p: p["secret_references"]["issuer"].update(owner_role="gateway"),
        lambda p: p["network"].update(redirects="allow"),
        lambda p: p["filesystem"].update(secret_file_mode="0644"),
    ),
)
def test_profile_tampering_denies_before_private_paths(tmp_path, mutation):
    manifest, artifact = _inputs(tmp_path)
    profile = copy.deepcopy(manifest["deployment_profile"])
    mutation(profile)
    with pytest.raises(ValueError, match="deployment profile"):
        validate_profile(profile, manifest, artifact)


def test_profile_digest_detects_replacement():
    manifest, _ = _inputs(Path("/tmp/profile-test"))
    assert manifest["deployment_profile_sha256"] == profile_sha256(manifest["deployment_profile"])
    changed = copy.deepcopy(manifest["deployment_profile"])
    changed["lifecycle"]["restart"] = "restore_authority"
    assert profile_sha256(changed) != manifest["deployment_profile_sha256"]


def test_secret_layout_rotation_is_atomic_and_interrupted_stage_is_ignored(tmp_path):
    manifest, _ = _inputs(tmp_path)
    keys = Path(manifest["keys_directory"])
    keys.mkdir(mode=0o700)
    for name in manifest["deployment_profile"]["secret_references"]:
        path = keys / name
        path.write_text("synthetic-initial")
        path.chmod(0o600)
    validate_secret_layout(manifest["deployment_profile"])
    staged = keys / "source-credential.next"
    staged.write_text("synthetic-rotated")
    staged.chmod(0o644)
    # An interrupted rotation leaves the current generation valid; the staged
    # file is not a referenced input and is never read by the runtime.
    validate_secret_layout(manifest["deployment_profile"])
    source = keys / "source-credential"
    staged.chmod(0o600)
    staged.replace(source)
    validate_secret_layout(manifest["deployment_profile"])
    assert "synthetic-initial" not in json.dumps(manifest["deployment_profile"])
    assert "synthetic-rotated" not in json.dumps(manifest["deployment_profile"])


@pytest.mark.parametrize("mutation", ("profile", "profile_hash", "secret_metadata"))
def test_restart_revalidates_profile_and_secret_custody_before_reconstruction(
    tmp_path, monkeypatch, mutation
):
    manifest, artifact = _inputs(tmp_path)
    state = Path(manifest["state_directory"])
    keys = Path(manifest["keys_directory"])
    state.mkdir(mode=0o700)
    keys.mkdir(mode=0o700)
    for name in manifest["deployment_profile"]["secret_references"]:
        path = keys / name
        path.write_text("synthetic-private-input")
        path.chmod(0o600)
    certificate = Path(manifest["certificate"])
    certificate.write_text("synthetic-certificate")
    certificate.chmod(0o600)
    manifest["certificate_sha256"] = hashlib.sha256(certificate.read_bytes()).hexdigest()

    if mutation == "profile":
        manifest["deployment_profile"]["network"]["redirects"] = "allow"
        manifest["deployment_profile_sha256"] = profile_sha256(
            manifest["deployment_profile"]
        )
    elif mutation == "profile_hash":
        manifest["deployment_profile_sha256"] = "0" * 64
    else:
        (keys / "source-credential").chmod(0o640)

    runtime = deployment.Deployment.__new__(deployment.Deployment)
    runtime.transition = threading.RLock()
    runtime.manifest = manifest
    runtime.blocked = False
    runtime.processes = {}
    cutoffs = []

    def cutoff():
        cutoffs.append("existing-authority")
        runtime.blocked = True
        return {
            "status": "blocked",
            "LIVE_PILOT_READY": False,
            "execution_allowed": False,
        }

    def forbidden(*_args, **_kwargs):
        pytest.fail("invalid restart inputs must not reconstruct services")

    runtime._cutoff = cutoff
    runtime.service = forbidden
    monkeypatch.setattr(deployment, "artifact_identity", lambda: artifact)
    monkeypatch.setattr(deployment.subprocess, "run", forbidden)

    assert runtime.restart() == {
        "status": "blocked",
        "LIVE_PILOT_READY": False,
        "execution_allowed": False,
    }
    assert cutoffs == ["existing-authority"]
    assert runtime.blocked is True
