"""Strict, versioned deployment contract for the installed pilot runtime.

The profile contains references and policy only; it never contains secret
values.  Its shape is deliberately closed so an operator cannot select a
different module, callable, mount, role, or network policy through config.
"""

import os
import stat
from pathlib import Path

from .broker_contract import digest, exact

PROFILE_VERSION = 1
ENTRYPOINT = {
    "console_script": "orion-runtime",
    "module": "orion.pilot.deployment",
    "interpreter_mode": "isolated_python",
}

ROLE_POLICY = {
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
    "audit-signing": "audit",
    "evidence-signing": "evidence",
    "issuer": "authorization",
    "worker-secret": "authorization",
    "source-credential": "gateway",
}


def profile_for_manifest(manifest, artifact):
    """Return the canonical profile derived from already validated inputs."""
    root = str(Path(manifest["state_directory"]).absolute())
    keys = str(Path(manifest["keys_directory"]).absolute())
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
    return {
        "version": PROFILE_VERSION,
        "artifact": {
            "name": artifact["name"],
            "version": artifact["version"],
            "record_sha256": artifact["record_sha256"],
        },
        "entrypoint": dict(ENTRYPOINT),
        "roles": roles,
        "filesystem": {
            "state_directory": root,
            "keys_directory": keys,
            "directory_mode": "0700",
            "secret_file_mode": "0600",
            "persistent_state": ["audit", "evidence"],
        },
        "network": {
            "mode": "private_kernel_egress",
            "approved_host": manifest["host"],
            "approved_port": 44443,
            "redirects": "deny",
            "proxies": "deny",
            "alternate_ports": "deny",
        },
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
        },
    }


def validate_profile(profile, manifest, artifact):
    """Validate the complete profile before private paths or services are used."""
    expected = profile_for_manifest(manifest, artifact)
    shape = exact(
        profile,
        (
            "version",
            "artifact",
            "entrypoint",
            "roles",
            "filesystem",
            "network",
            "secret_references",
            "lifecycle",
        ),
    )
    if shape != expected:
        raise ValueError("deployment profile does not match installed runtime")
    if profile["version"] != PROFILE_VERSION:
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
