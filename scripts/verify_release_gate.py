"""Offline exact-artifact release gate; approvals are detached Ed25519 signatures."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import subprocess
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

REQUIRED_CHECKS = frozenset(
    {
        "build",
        "dependency_integrity",
        "vulnerability_assessment",
        "architecture_review",
        "security_review",
        "compatibility",
        "full_tests",
        "deployment_recovery",
        "rollback",
    }
)
REQUIRED_ROLES = frozenset({"security", "architecture", "release_owner"})
REQUIRED_DISTS = frozenset(
    {
        "originorion_core",
        "originorion_connector_sdk",
        "originorion_connector_erpnext_discovery",
        "originorion_connector_synthetic",
        "originorion_erpnext_pilot_compat",
    }
)
HEX64 = re.compile(r"[0-9a-f]{64}\Z")
HEX40 = re.compile(r"[0-9a-f]{40}\Z")


class ReleaseDenied(ValueError):
    """A required release identity, check, or trusted approval is absent."""


def _object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseDenied("unreadable release evidence") from exc
    if not isinstance(value, dict):
        raise ReleaseDenied("release evidence must be an object")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ReleaseDenied("release artifact unavailable") from exc
    return digest.hexdigest()


def _git(source_dir: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(source_dir), *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise ReleaseDenied("source checkout unavailable") from exc
    return result.stdout.strip()


def _verify_inventory(directory: Path, inventory: dict, *, label: str) -> None:
    for name, expected in inventory.items():
        if (
            not isinstance(name, str)
            or Path(name).name != name
            or not isinstance(expected, str)
            or not HEX64.fullmatch(expected)
        ):
            raise ReleaseDenied(f"unsafe {label} inventory")
        path = directory / name
        if path.is_symlink() or not path.is_file() or _sha256(path) != expected:
            raise ReleaseDenied(f"{label} hash mismatch")


def verify(
    manifest_path: Path,
    artifacts_dir: Path,
    trust_path: Path,
    approvals_dir: Path,
    source_dir: Path,
    records_dir: Path,
) -> dict:
    """Verify one immutable manifest and three independently trusted approvals."""
    manifest = _object(manifest_path)
    if manifest.get("schema") != "orion.release-candidate.v1":
        raise ReleaseDenied("unsupported release manifest")
    if not isinstance(manifest.get("source_sha"), str) or not HEX40.fullmatch(
        manifest["source_sha"]
    ):
        raise ReleaseDenied("invalid source SHA")
    if not isinstance(manifest.get("tree"), str) or not HEX40.fullmatch(manifest["tree"]):
        raise ReleaseDenied("invalid source tree")
    if (
        _git(source_dir, "rev-parse", "HEAD") != manifest["source_sha"]
        or _git(source_dir, "rev-parse", "HEAD^{tree}") != manifest["tree"]
        or _git(source_dir, "status", "--porcelain", "--untracked-files=all")
    ):
        raise ReleaseDenied("source checkout does not match clean manifest identity")
    author = manifest.get("source_author")
    if (not isinstance(author, str)
            or author != _git(source_dir, "show", "-s", "--format=%an <%ae>", "HEAD")):
        raise ReleaseDenied("source author does not match exact commit")
    version = manifest.get("version")
    if not isinstance(version, str) or not re.fullmatch(r"0\.2\.0rc[1-9][0-9]*", version):
        raise ReleaseDenied("unsupported candidate version")
    checks = manifest.get("checks")
    if not isinstance(checks, dict) or any(
        checks.get(name) != "PASS" for name in REQUIRED_CHECKS
    ):
        raise ReleaseDenied("mandatory release check failed or missing")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict) or not artifacts:
        raise ReleaseDenied("missing artifact inventory")
    component_versions = manifest.get("component_versions")
    if not isinstance(component_versions, dict) or set(component_versions) != REQUIRED_DISTS:
        raise ReleaseDenied("missing component version inventory")
    for distribution in REQUIRED_DISTS:
        component_version = component_versions[distribution]
        if not isinstance(component_version, str) or not re.fullmatch(
            r"0\.2\.0rc[1-9][0-9]*", component_version
        ):
            raise ReleaseDenied("invalid component version")
        stem = f"{distribution}-{component_version}"
        wheels = [name for name in artifacts if name.startswith(stem + "-")
                  and name.endswith(".whl")]
        if len(wheels) != 1:
            raise ReleaseDenied("missing required wheel")
        if f"{stem}.tar.gz" not in artifacts:
            raise ReleaseDenied("missing required source archive")
        if any(name.startswith(distribution + "-") and name not in
               {wheels[0], f"{stem}.tar.gz"} for name in artifacts):
            raise ReleaseDenied("unexpected component artifact version")
    _verify_inventory(artifacts_dir, artifacts, label="artifact")
    records = manifest.get("records")
    if not isinstance(records, dict) or set(records) != REQUIRED_CHECKS | {"known_risks"}:
        raise ReleaseDenied("missing release evidence records")
    for name, expected in records.items():
        _verify_inventory(records_dir, {f"{name}.json": expected}, label="record")

    trust = _object(trust_path)
    keys = trust.get("keys")
    if not isinstance(keys, dict):
        raise ReleaseDenied("missing independent trust store")
    manifest_digest = _sha256(manifest_path)
    seen_identities: set[str] = set()
    approvals: dict[str, str] = {}
    for role in sorted(REQUIRED_ROLES):
        approval = _object(approvals_dir / f"{role}.json")
        identity = approval.get("reviewer_identity")
        key_id = approval.get("key_id")
        if (
            approval.get("role") != role
            or approval.get("decision") != "APPROVE"
            or approval.get("manifest_sha256") != manifest_digest
            or not isinstance(identity, str)
            or not identity.strip()
            or identity == author
            or identity in seen_identities
            or not isinstance(key_id, str)
        ):
            raise ReleaseDenied("missing or non-independent approval")
        trusted = keys.get(key_id)
        if not isinstance(trusted, dict) or trusted.get("role") != role or (
            trusted.get("reviewer_identity") != identity
        ):
            raise ReleaseDenied("approval signer is untrusted")
        signed = {
            "decision": "APPROVE",
            "key_id": key_id,
            "manifest_sha256": manifest_digest,
            "reviewer_identity": identity,
            "role": role,
        }
        try:
            public_key = Ed25519PublicKey.from_public_bytes(
                base64.b64decode(trusted["public_key"], validate=True)
            )
            signature = base64.b64decode(approval["signature"], validate=True)
            public_key.verify(
                signature,
                json.dumps(signed, sort_keys=True, separators=(",", ":")).encode(),
            )
        except (KeyError, TypeError, ValueError, InvalidSignature) as exc:
            raise ReleaseDenied("approval signature invalid") from exc
        seen_identities.add(identity)
        approvals[role] = identity
    return {
        "release_gate": "PASS",
        "source_sha": manifest["source_sha"],
        "tree": manifest["tree"],
        "manifest_sha256": manifest_digest,
        "artifact_count": len(artifacts),
        "approval_roles": sorted(approvals),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--trust", type=Path, required=True)
    parser.add_argument("--approvals", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--records", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = verify(
            args.manifest, args.artifacts, args.trust, args.approvals, args.source, args.records
        )
    except ReleaseDenied as exc:
        print(json.dumps({"release_gate": "DENIED", "reason": str(exc)}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
