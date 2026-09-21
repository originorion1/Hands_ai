"""Trusted production-discovery inputs for the installed read-only runtime.

These private operator records are not grants and never come from ERP/package
responses.  They bind the reviewed destination and host to an existing
metadata-only deployment before any state creation, DNS lookup, or source I/O.
"""

import hmac
import ipaddress
import re
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from ..contracts import utc_now
from .broker_contract import authenticate, decode, digest, exact, private_bytes

DESTINATION_VERSION = "erpnext-reviewed-https-destination-v1"
LEDGER_VERSION = "orion-read-only-discovery-access-ledger-v1"
ATTESTATION_VERSION = "orion-reviewed-discovery-host-v1"
APPROVAL_VERSION = "orion-trusted-controller-approval-v1"
QUALIFICATION_ADDRESSES = {"192.0.2.2", "fd42:6f72:696f::2"}
MAX_ADDRESSES = 8
MAX_LEDGER_SECONDS = 24 * 60 * 60


def _reference(value, label):
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{label} digest required")
    return value


def _time(value, label):
    if type(value) is not str:
        raise ValueError(f"{label} time required")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{label} timezone required")
    return parsed


def _identity(value, label, *, limit=128):
    if (
        type(value) is not str
        or not 1 <= len(value) <= limit
        or not value.isascii()
        or any(ord(character) < 0x21 or ord(character) > 0x7E for character in value)
    ):
        raise ValueError(f"bounded {label} required")
    return value


def destination_from(value, certificate_sha256):
    """Validate one exact HTTPS origin independently of request/package data."""
    value = exact(
        value,
        (
            "version",
            "network_mode",
            "origin",
            "hostname",
            "port",
            "addresses",
            "tls_server_name",
            "certificate_sha256",
            "resolution_observed_at",
            "resolution_expires_at",
        ),
    )
    if value["version"] != DESTINATION_VERSION:
        raise ValueError("unsupported destination contract")
    mode = value["network_mode"]
    if mode not in ("local_qualification", "reviewed_external"):
        raise ValueError("explicit destination network mode required")
    hostname = value["hostname"]
    if (
        type(hostname) is not str
        or hostname != hostname.lower()
        or len(hostname) > 253
        or not re.fullmatch(
            r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
            r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?",
            hostname,
        )
    ):
        raise ValueError("canonical TLS hostname required")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise ValueError("TLS DNS hostname required")
    port = value["port"]
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("bounded destination port required")
    origin = value["origin"]
    parsed = urlsplit(origin) if type(origin) is str else None
    if (
        parsed is None
        or parsed.scheme != "https"
        or parsed.hostname != hostname
        or parsed.port != port
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or origin != f"https://{hostname}:{port}"
    ):
        raise ValueError("exact HTTPS origin required")
    addresses = value["addresses"]
    if type(addresses) is not list or not 1 <= len(addresses) <= MAX_ADDRESSES:
        raise ValueError("bounded reviewed address set required")
    parsed_addresses = []
    for address in addresses:
        if type(address) is not str:
            raise ValueError("numeric reviewed address required")
        try:
            item = ipaddress.ip_address(address)
        except ValueError:
            raise ValueError("numeric reviewed address required") from None
        if item.is_unspecified or item.is_multicast or item.is_link_local or item.is_loopback:
            raise ValueError("routable reviewed address required")
        parsed_addresses.append(item)
    canonical = [str(item) for item in sorted(parsed_addresses, key=lambda item: (item.version, int(item)))]
    if addresses != canonical or len(set(addresses)) != len(addresses):
        raise ValueError("canonical reviewed address set required")
    if value["tls_server_name"] != hostname:
        raise ValueError("TLS server identity mismatch")
    if (
        _reference(value["certificate_sha256"], "certificate")
        != _reference(certificate_sha256, "manifest certificate")
    ):
        raise ValueError("destination trust mismatch")
    observed = _time(value["resolution_observed_at"], "resolution observation")
    expires = _time(value["resolution_expires_at"], "resolution expiry")
    if expires <= observed or expires - observed > timedelta(seconds=MAX_LEDGER_SECONDS):
        raise ValueError("bounded resolution lifetime required")
    if mode == "local_qualification":
        if not hostname.endswith(".test") or set(addresses) - QUALIFICATION_ADDRESSES or port != 44443:
            raise ValueError("local qualification destination required")
    elif hostname.endswith(".test") or set(addresses) & QUALIFICATION_ADDRESSES:
        raise ValueError("reviewed external destination required")
    return value


def access_ledger_from(value, manifest, destination, *, now=None):
    """Validate exact customer/session scope without restoring any authority."""
    value = exact(
        value,
        (
            "version",
            "session_id",
            "deployment_identity",
            "artifact_record_sha256",
            "destination_sha256",
            "metadata_binding",
            "transition_envelope_sha256",
            "tenant_id",
            "company",
            "source_id",
            "credential_reference",
            "issuer_reference",
            "site_schema",
            "exclusions",
            "limits",
            "prior_consumed_sessions",
            "issued_at",
            "expires_at",
        ),
    )
    if value["version"] != LEDGER_VERSION:
        raise ValueError("unsupported access ledger")
    metadata = manifest["configs"][0]
    grant = metadata["grant"]
    request = grant["request"]
    policy = manifest["grant_transition"]
    expected = {
        "deployment_identity": manifest["deployment_identity"],
        "artifact_record_sha256": manifest["artifact_record_sha256"],
        "destination_sha256": digest(destination),
        "metadata_binding": digest(metadata),
        "transition_envelope_sha256": digest(policy),
        "tenant_id": request["tenant_id"],
        "company": request["company"],
        "source_id": request["source_id"],
        "credential_reference": metadata["secret_reference"],
        "issuer_reference": metadata["auth_reference"],
    }
    if any(value[name] != expected[name] for name in expected):
        raise ValueError("access ledger scope mismatch")
    if value["source_id"] != destination["origin"]:
        raise ValueError("access ledger destination mismatch")
    _identity(value["session_id"], "session identity")
    if value["site_schema"] != "erpnext-read-only-v1":
        raise ValueError("reviewed site schema required")
    exclusions = value["exclusions"]
    if (
        type(exclusions) is not list
        or exclusions != sorted(set(exclusions))
        or not 1 <= len(exclusions) <= 64
        or any(_identity(item, "exclusion", limit=128) != item for item in exclusions)
    ):
        raise ValueError("bounded ledger exclusions required")
    limits = exact(
        value["limits"],
        ("max_requests", "response_bytes", "total_response_bytes", "duration_seconds"),
    )
    configured = metadata["limits"]
    if (
        type(limits["max_requests"]) is not int
        or limits["max_requests"] != configured["max_requests"]
        or type(limits["response_bytes"]) is not int
        or limits["response_bytes"] != configured["response_bytes"]
        or type(limits["total_response_bytes"]) is not int
        or limits["total_response_bytes"] != configured["total_response_bytes"]
        or type(limits["duration_seconds"]) is not int
        or not 1 <= limits["duration_seconds"] <= MAX_LEDGER_SECONDS
    ):
        raise ValueError("access ledger limits must equal canonical enforcement")
    prior = value["prior_consumed_sessions"]
    if (
        type(prior) is not list
        or len(prior) > 128
        or prior != sorted(set(prior))
        or value["session_id"] in prior
        or any(_reference(item, "consumed session") != item for item in prior)
    ):
        raise ValueError("consumed session lineage required")
    issued = _time(value["issued_at"], "ledger issue")
    expires = _time(value["expires_at"], "ledger expiry")
    current = now or utc_now()
    if (
        expires <= issued
        or expires != _time(configured["expires_at"], "canonical limit expiry")
        or expires - issued > timedelta(seconds=limits["duration_seconds"])
        or expires - issued > timedelta(seconds=MAX_LEDGER_SECONDS)
    ):
        raise ValueError("bounded access ledger lifetime required")
    if current < issued or current >= expires or current >= _time(
        destination["resolution_expires_at"], "resolution expiry"
    ):
        raise ValueError("stale access ledger or destination denied")
    if expires > _time(destination["resolution_expires_at"], "resolution expiry"):
        raise ValueError("access ledger exceeds reviewed resolution lifetime")
    return value


def host_attestation_from(value, manifest, destination, ledger, *, now=None):
    """Validate exact host/artifact/profile evidence, never package booleans."""
    value = exact(
        value,
        (
            "version",
            "deployment_identity",
            "artifact_record_sha256",
            "deployment_profile_sha256",
            "installed_qualification_sha256",
            "destination_sha256",
            "access_ledger_sha256",
            "network_namespace",
            "host_network_namespace",
            "service",
            "controls",
            "observed_at",
            "expires_at",
        ),
    )
    if value["version"] != ATTESTATION_VERSION:
        raise ValueError("unsupported host attestation")
    expected = {
        "deployment_identity": manifest["deployment_identity"],
        "artifact_record_sha256": manifest["artifact_record_sha256"],
        "deployment_profile_sha256": manifest["deployment_profile_sha256"],
        "destination_sha256": digest(destination),
        "access_ledger_sha256": digest(ledger),
    }
    if any(value[name] != expected[name] for name in expected):
        raise ValueError("host attestation binding mismatch")
    _reference(value["installed_qualification_sha256"], "installed qualification")
    network = _identity(value["network_namespace"], "reviewed network namespace")
    host_network = _identity(value["host_network_namespace"], "host network namespace")
    if network == host_network:
        raise ValueError("distinct reviewed network namespace required")
    profile = manifest["deployment_profile"]
    service = exact(
        value["service"],
        ("interpreter", "module", "manifest_path", "working_directory", "environment"),
    )
    entrypoint = profile["entrypoint"]
    if service != {
        "interpreter": entrypoint["interpreter"],
        "module": entrypoint["module"],
        "manifest_path": entrypoint["manifest_path"],
        "working_directory": entrypoint["working_directory"],
        "environment": entrypoint["environment"],
    }:
        raise ValueError("host service attestation mismatch")
    controls = value["controls"]
    expected_controls = [
        "exact-address-default-deny-egress",
        "gateway-only-network-role",
        "private-owner-only-custody",
        "rootless-unprivileged-service",
        "stop-terminates-egress-role",
        "wheel-only-isolated-module",
    ]
    if controls != expected_controls:
        raise ValueError("complete host controls attestation required")
    observed = _time(value["observed_at"], "host observation")
    expires = _time(value["expires_at"], "host attestation expiry")
    current = now or utc_now()
    if expires <= observed or expires - observed > timedelta(seconds=MAX_LEDGER_SECONDS):
        raise ValueError("bounded host attestation lifetime required")
    if expires != _time(ledger["expires_at"], "access ledger expiry"):
        raise ValueError("host attestation lifetime mismatch")
    if current < observed or current >= expires:
        raise ValueError("stale host attestation denied")
    return value


def approval_from(value, subject, purpose, key, *, now=None):
    """Require separate controller approval for one exact private record."""
    value = exact(
        value,
        ("version", "purpose", "subject_sha256", "controller_id", "issued_at", "expires_at", "mac"),
    )
    if value["version"] != APPROVAL_VERSION or value["purpose"] != purpose:
        raise ValueError("trusted controller approval purpose denied")
    payload = {name: value[name] for name in value if name != "mac"}
    if value["subject_sha256"] != digest(subject):
        raise ValueError("trusted controller approval subject mismatch")
    _identity(value["controller_id"], "controller identity")
    issued = _time(value["issued_at"], "approval issue")
    expires = _time(value["expires_at"], "approval expiry")
    current = now or utc_now()
    if expires <= issued or expires - issued > timedelta(seconds=MAX_LEDGER_SECONDS):
        raise ValueError("bounded controller approval lifetime required")
    if current < issued or current >= expires:
        raise ValueError("stale controller approval denied")
    expected = authenticate(key, purpose, payload)
    if type(value["mac"]) is not str or not hmac.compare_digest(value["mac"], expected):
        raise ValueError("trusted controller approval authentication denied")
    return value


def load_production_evidence(manifest, *, now=None):
    """Load and authenticate private evidence before deployment state is created."""
    destination = destination_from(manifest["destination"], manifest["certificate_sha256"])
    ledger = access_ledger_from(
        decode(private_bytes(manifest["access_ledger"])), manifest, destination, now=now
    )
    access_approval = approval_from(
        decode(private_bytes(manifest["access_approval"])),
        ledger,
        "discovery_access_ledger",
        private_bytes(Path(manifest["keys_directory"]) / "issuer"),
        now=now,
    )
    attestation = host_attestation_from(
        decode(private_bytes(manifest["host_attestation"])),
        manifest,
        destination,
        ledger,
        now=now,
    )
    host_approval = approval_from(
        decode(private_bytes(manifest["host_approval"])),
        attestation,
        "discovery_host_attestation",
        private_bytes(Path(manifest["keys_directory"]) / "deployment-approval"),
        now=now,
    )
    if (
        _time(access_approval["expires_at"], "access approval expiry")
        < _time(ledger["expires_at"], "access ledger expiry")
        or _time(host_approval["expires_at"], "host approval expiry")
        < _time(attestation["expires_at"], "host attestation expiry")
    ):
        raise ValueError("controller approval expires before approved subject")
    return {
        "destination": destination,
        "access_ledger_sha256": digest(ledger),
        "host_attestation_sha256": digest(attestation),
        "session_id_sha256": digest(ledger["session_id"]),
        "network_namespace": attestation["network_namespace"],
        "host_network_namespace": attestation["host_network_namespace"],
        "qualification": destination["network_mode"] == "local_qualification",
    }
