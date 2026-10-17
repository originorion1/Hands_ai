"""Prepare an atomic localhost-only development TLS pair in private custody.

This does not restart a service, trust the certificate system-wide or expose a
network endpoint. The private key never leaves the operator-selected root.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import secrets
import stat
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

VALID_DAYS = 30


class TLSCustodyDenied(ValueError):
    """The local certificate root or replacement is unsafe."""


def _private_root(root: Path) -> None:
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise TLSCustodyDenied("private TLS root unavailable")
    metadata = root.stat()
    if (metadata.st_uid != os.getuid() or not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_mode & 0o077):
        raise TLSCustodyDenied("private TLS root permissions unsafe")


def _write_private(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)


def _certificate(now: datetime) -> tuple[bytes, bytes]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=VALID_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=False, key_encipherment=True,
            data_encipherment=False, key_agreement=False, key_cert_sign=True,
            crl_sign=False, encipher_only=False, decipher_only=False,
        ), critical=True)
        .add_extension(x509.SubjectAlternativeName([
            x509.DNSName("localhost"),
            x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
        ]), critical=False)
        .sign(key, hashes.SHA256())
    )
    return (
        certificate.public_bytes(serialization.Encoding.PEM),
        key.private_bytes(serialization.Encoding.PEM,
                          serialization.PrivateFormat.PKCS8,
                          serialization.NoEncryption()),
    )


def prepare(root: Path, *, now: datetime | None = None) -> dict:
    """Swap a complete cert/key pair through one atomic `current` symlink."""
    _private_root(root)
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        raise TLSCustodyDenied("timezone-aware clock required")
    moment = moment.astimezone(UTC)
    current = root / "current"
    if current.exists() and (not current.is_symlink()
                             or not current.readlink().name.startswith("cert-")):
        raise TLSCustodyDenied("unreviewed current TLS path")
    if current.is_symlink() and (current.readlink().is_absolute()
                                 or len(current.readlink().parts) != 1):
        raise TLSCustodyDenied("current TLS path escapes private root")
    certificate, key = _certificate(moment)
    stage = Path(tempfile.mkdtemp(prefix="stage-", dir=root))
    version = f"cert-{moment.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(8)}"
    target = root / version
    replacement = root / f".next-{secrets.token_hex(8)}"
    try:
        os.chmod(stage, 0o700)
        _write_private(stage / "cert.pem", certificate)
        _write_private(stage / "key.pem", key)
        parsed = x509.load_pem_x509_certificate((stage / "cert.pem").read_bytes())
        loaded_key = serialization.load_pem_private_key(
            (stage / "key.pem").read_bytes(), password=None
        )
        if parsed.public_key().public_numbers() != loaded_key.public_key().public_numbers():
            raise TLSCustodyDenied("certificate/key mismatch")
        directory = os.open(stage, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        os.replace(stage, target)
        os.symlink(version, replacement)
        os.replace(replacement, current)
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if replacement.is_symlink():
            replacement.unlink()
        if stage.exists():
            for child in stage.iterdir():
                child.unlink()
            stage.rmdir()
    return {
        "certificate_sha256": hashlib.sha256(certificate).hexdigest(),
        "not_after_utc": parsed.not_valid_after_utc.isoformat(),
        "current": str(current),
        "service_restarted": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(prepare(args.root), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
