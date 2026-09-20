"""Strict, versioned deployment contract for the installed pilot runtime.

The profile contains references and policy only; it never contains secret
values.  Its shape is deliberately closed so an operator cannot select a
different module, callable, mount, role, or network policy through config.
"""

import os
import stat
from pathlib import Path

from .broker_contract import digest, exact, transition_policy_from
from .production import (
    APPROVAL_VERSION,
    ATTESTATION_VERSION,
    DESTINATION_VERSION,
    LEDGER_VERSION,
    destination_from,
)
from .progress_witness import (
    WITNESS_VERSION,
    deployment_identity_for_manifest,
    witness_identity,
    witness_streams,
)

PROFILE_VERSION = 3
CANDIDATE_PROFILE_VERSION = 4
TRANSITION_PROFILE_VERSION = 5
PRODUCTION_PROFILE_VERSION = 6
ENTRYPOINT_MODULE = "orion.pilot.deployment"

ROLE_POLICY = {
    "witness": {
        "identity": "progress-witness",
        "network": "none",
        "state": "witness:rw",
        "secret_refs": ("witness-signing",),
    },
    "audit": {
        "identity": "audit-custody",
        "network": "none",
        "state": "audit:rw",
        "secret_refs": ("audit-signing",),
    },
    "evidence": {
        "identity": "evidence-custody",
        "network": "none",
        "state": "evidence:rw",
        "secret_refs": ("evidence-signing",),
    },
    "authorization": {
        "identity": "authorization-custody",
        "network": "none",
        "state": "none",
        "secret_refs": ("issuer", "worker-secret"),
    },
    "gateway": {
        "identity": "credential-gateway",
        "network": "approved-destination-only",
        "state": "none",
        "secret_refs": ("source-credential",),
    },
    "acquisition": {
        "identity": "acquisition-broker",
        "network": "none",
        "state": "none",
        "secret_refs": (),
    },
    "reasoning": {
        "identity": "reasoning-process",
        "network": "none",
        "state": "none",
        "secret_refs": (),
    },
}

SECRET_OWNERS = {
    "witness-signing": "witness",
    "audit-signing": "audit",
    "evidence-signing": "evidence",
    "issuer": "authorization",
    "worker-secret": "authorization",
    "source-credential": "gateway",
}


def profile_for_manifest(manifest, artifact, manifest_path):
    """Return the canonical profile derived from already validated inputs."""
    root = str(Path(manifest["state_directory"]).absolute())
    keys = str(Path(manifest["keys_directory"]).absolute())
    witness = str(Path(manifest["witness_directory"]).absolute())
    expected_identity = deployment_identity_for_manifest(manifest, artifact)
    if manifest.get("deployment_identity") != expected_identity:
        raise ValueError("stable deployment identity required")
    manifest_path = str(Path(manifest_path).resolve())
    roles = {
        name: {
            "identity": policy["identity"],
            "network": policy["network"],
            "state": policy["state"],
            "secret_refs": list(policy["secret_refs"]),
            "mounts": {
                "configs": "ro",
                "endpoint": "rw",
                "keys": "ro" if policy["secret_refs"] else "none",
            },
        }
        for name, policy in ROLE_POLICY.items()
    }
    version = manifest.get("version")
    candidate = version in (4, 5, 6)
    network = {
        "mode": "private_kernel_egress",
        "approved_host": manifest["host"],
        "approved_port": 44443,
        "redirects": "deny",
        "proxies": "deny",
        "alternate_ports": "deny",
    }
    if candidate:
        network["protocol"] = "erpnext_read_only_v1"
    if version == 6:
        destination = destination_from(
            manifest["destination"], manifest["certificate_sha256"]
        )
        network = {
            "mode": "reviewed_exact_destination_egress",
            "network_mode": destination["network_mode"],
            "origin": destination["origin"],
            "hostname": destination["hostname"],
            "approved_addresses": destination["addresses"],
            "approved_port": destination["port"],
            "tls_server_name": destination["tls_server_name"],
            "certificate_sha256": destination["certificate_sha256"],
            "resolution_observed_at": destination["resolution_observed_at"],
            "resolution_expires_at": destination["resolution_expires_at"],
            "protocol": "erpnext_read_only_v1",
            "dns": "exact_reviewed_set_or_deny",
            "redirects": "deny",
            "proxies": "deny",
            "alternate_ports": "deny",
        }
        roles["authorization"]["secret_refs"].append("deployment-approval")
    profile = {
        "version": (
            PRODUCTION_PROFILE_VERSION
            if version == 6
            else TRANSITION_PROFILE_VERSION
            if version == 5
            else CANDIDATE_PROFILE_VERSION
            if candidate
            else PROFILE_VERSION
        ),
        "artifact": {
            "name": artifact["name"],
            "version": artifact["version"],
            "record_sha256": artifact["record_sha256"],
            "installed_prefix": artifact["installed_prefix"],
            "package_root": artifact["package_root"],
        },
        "entrypoint": {
            "console_script": "orion-runtime",
            "console_script_scope": "inspection_only",
            "module": ENTRYPOINT_MODULE,
            "interpreter": artifact["interpreter"],
            "interpreter_mode": "isolated_python_module",
            "manifest_path": manifest_path,
            "working_directory": "/",
            "environment": "reject_python_and_loader_controls",
        },
        "roles": roles,
        "filesystem": {
            "state_directory": root,
            "keys_directory": keys,
            "witness_directory": witness,
            "directory_mode": "0700",
            "secret_file_mode": "0600",
            "persistent_state": ["audit", "evidence"],
            "independent_witness_state": "witness",
            "witness_enrollment_receipt": str(Path(root) / "witness-enrollment"),
        },
        "witness": {
            "version": WITNESS_VERSION,
            "deployment_identity": expected_identity,
            "witness_identity": witness_identity(expected_identity),
            "streams": witness_streams(
                manifest["configs"], manifest.get("grant_transition")
            ),
            "rollback_set": [str(Path(root) / "audit"), str(Path(root) / "evidence")],
            "storage_outside_rollback_set": witness,
            "whole_host_rollback_protection": False,
        },
        "network": network,
        "secret_references": {
            name: {"path": str(Path(keys) / name), "owner_role": owner}
            for name, owner in SECRET_OWNERS.items()
        },
        "lifecycle": {
            "startup": "validate_all_or_deny",
            "shutdown": "cutoff_then_durable_stop",
            "restart": "revalidate_without_authority_restore",
            "emergency_stop": "remove_egress_and_terminate",
            "authority_restore": "forbidden",
            "witness_enrollment": "explicit_operator_bootstrap",
            "witness_disagreement": "cutoff_and_deny",
        },
    }
    if version == 6:
        profile["secret_references"]["deployment-approval"] = {
            "path": str(Path(keys) / "deployment-approval"),
            "owner_role": "authorization",
        }
        profile["production_discovery"] = {
            "destination_version": DESTINATION_VERSION,
            "access_ledger_version": LEDGER_VERSION,
            "host_attestation_version": ATTESTATION_VERSION,
            "controller_approval_version": APPROVAL_VERSION,
            "access_ledger": str(Path(manifest["access_ledger"]).resolve()),
            "access_approval": str(Path(manifest["access_approval"]).resolve()),
            "host_attestation": str(Path(manifest["host_attestation"]).resolve()),
            "host_approval": str(Path(manifest["host_approval"]).resolve()),
            "startup": "unarmed_metadata_only",
            "record_access": "separate_witnessed_transition_then_arm",
            "customer_writes": "forbidden",
        }
    if version in (5, 6):
        policy = transition_policy_from(manifest["grant_transition"])
        profile["authorization_transition"] = {
            "version": policy["version"],
            "envelope_sha256": digest(policy),
            "generation_limit": 1,
            "initial_state": "unprovisioned_no_record_authority",
            "commit_order": ["evidence", "audit", "authorization", "gateway", "acquisition"],
            "restart": "validate_complete_lineage_then_unarmed",
        }
    return profile


def validate_profile(profile, manifest, artifact, manifest_path):
    """Validate the complete profile before private paths or services are used."""
    expected = profile_for_manifest(manifest, artifact, manifest_path)
    shape = exact(
        profile,
        (
            "version",
            "artifact",
            "entrypoint",
            "roles",
            "filesystem",
            "witness",
            "network",
            "secret_references",
            "lifecycle",
        )
        + (("production_discovery",) if manifest.get("version") == 6 else ())
        + (("authorization_transition",) if manifest.get("version") in (5, 6) else ()),
    )
    if shape != expected:
        raise ValueError("deployment profile does not match installed runtime")
    expected_version = (
        PRODUCTION_PROFILE_VERSION
        if manifest.get("version") == 6
        else TRANSITION_PROFILE_VERSION
        if manifest.get("version") == 5
        else CANDIDATE_PROFILE_VERSION
        if manifest.get("version") == 4
        else PROFILE_VERSION
    )
    if profile["version"] != expected_version:
        raise ValueError("unsupported deployment profile version")
    if digest(profile) == "":  # pragma: no cover - defensive contract assertion
        raise ValueError("deployment profile digest unavailable")
    return profile


def validate_secret_layout(profile):
    """Validate secret custody inputs by metadata only; never read values."""
    for reference in profile["secret_references"].values():
        path = Path(reference["path"])
        info = path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
            or path.is_symlink()
        ):
            raise ValueError("secret custody metadata denied")


def profile_sha256(profile):
    """Canonical profile identity for operator records; no secret values."""
    return digest(profile)
