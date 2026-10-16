"""Prepare a short-lived report token from an encrypted, operator-held key.

This is an offline operator tool, not a login endpoint. It creates no key and
does not print the token. The signing key and passphrase must be supplied by
the separately controlled identity host, never the report host or Vercel.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import stat
import time
from pathlib import Path

from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key

from orion.presentation.report_auth import ReportAccessPolicy
from orion.stores.private_files import read_private_file

_TOKEN_SECONDS = 300


class IssuanceError(ValueError):
    """The private issuance contract is unavailable or invalid."""


def _b64(body: bytes) -> str:
    return base64.urlsafe_b64encode(body).rstrip(b"=").decode("ascii")


def _read_fd(fd: int, *, limit: int) -> bytes:
    if type(fd) is not int or fd < 0:
        raise IssuanceError("issuance unavailable")
    body = os.read(fd, limit + 1)
    if not body or len(body) > limit:
        raise IssuanceError("issuance unavailable")
    return body


def issue_token(
    *,
    policy_path: Path,
    key_path: Path,
    subject: str,
    passphrase: bytes,
    output_path: Path,
    now: int | None = None,
) -> None:
    """Write one exclusive, owner-only bearer token; return no token material."""
    created = False
    try:
        policy = ReportAccessPolicy.from_private_file(policy_path)
        if type(subject) is not str or subject not in policy.allowed_subjects:
            raise IssuanceError("issuance unavailable")
        if not passphrase or len(passphrase) > 4096:
            raise IssuanceError("issuance unavailable")
        if not key_path.is_absolute() or ".." in key_path.parts:
            raise IssuanceError("issuance unavailable")
        if key_path.parent.resolve(strict=True) != key_path.parent:
            raise IssuanceError("issuance unavailable")
        key_body = read_private_file(key_path, label="issuer key", maximum_bytes=16 * 1024)
        key = load_pem_private_key(key_body, password=passphrase)
        if not isinstance(key, Ed25519PrivateKey):
            raise IssuanceError("issuance unavailable")
        policy.public_key.verify(key.sign(b"orion.report-key-match.v1"), b"orion.report-key-match.v1")
        current = int(time.time()) if now is None else now
        if type(current) is not int or current < 0:
            raise IssuanceError("issuance unavailable")
        header = _b64(b'{"alg":"EdDSA","typ":"JWT"}')
        claims = {
            "iss": policy.issuer,
            "aud": policy.audience,
            "sub": subject,
            "tenant_id": policy.tenant_id,
            "scope": "orion:report:read",
            "iat": current,
            "nbf": current,
            "exp": current + _TOKEN_SECONDS,
        }
        payload = _b64(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode())
        signing_input = f"{header}.{payload}".encode("ascii")
        token = f"{header}.{payload}.{_b64(key.sign(signing_input))}\n".encode("ascii")
        if not output_path.is_absolute() or ".." in output_path.parts:
            raise IssuanceError("issuance unavailable")
        parent = output_path.parent
        parent_stat = parent.stat()
        if (
            parent.resolve(strict=True) != parent
            or not stat.S_ISDIR(parent_stat.st_mode)
            or parent_stat.st_uid != os.geteuid()
            or stat.S_IMODE(parent_stat.st_mode) != 0o700
        ):
            raise IssuanceError("issuance unavailable")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(output_path, flags, 0o600)
        created = True
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(token)
            stream.flush()
            os.fsync(stream.fileno())
        directory = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except (OSError, TypeError, ValueError, InvalidSignature, UnsupportedAlgorithm) as exc:
        if created:
            try:
                output_path.unlink()
            except OSError:
                pass
        raise IssuanceError("issuance unavailable") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write one private report token")
    parser.add_argument("--policy", required=True, type=Path)
    parser.add_argument("--key", required=True, type=Path)
    parser.add_argument("--subject-fd", required=True, type=int)
    parser.add_argument("--passphrase-fd", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args(argv)
    try:
        if arguments.subject_fd == arguments.passphrase_fd:
            raise IssuanceError("issuance unavailable")
        subject = _read_fd(arguments.subject_fd, limit=256).decode("utf-8").removesuffix("\n")
        passphrase = _read_fd(arguments.passphrase_fd, limit=4096)
        issue_token(
            policy_path=arguments.policy,
            key_path=arguments.key,
            subject=subject,
            passphrase=passphrase,
            output_path=arguments.output,
        )
    except (IssuanceError, OSError, UnicodeError):
        parser.exit(1, "issuance unavailable\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
