"""Offline, administrator-only preparation of an ERPNext connector enrollment.

No connector is imported and no network request is made here. The backend vault
owns submitted secrets; the tenant registry receives only its opaque reference.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

from orion_connector_sdk import ReviewedConnector, discover

from .catalog import reviewed_catalog_entry
from .login import CustomerLogin
from .tenants import TenantRegistry


class EnrollmentDenied(ValueError):
    """The requested connector enrollment is outside reviewed scope."""


@dataclass(frozen=True, slots=True)
class EnrolledConnection:
    tenant_id: str
    connector: str
    endpoint: str
    company: str
    credential_ref: str
    execution_allowed: bool = False


def _origin(value: str) -> str:
    if type(value) is not str or len(value) > 255:
        raise EnrollmentDenied("reviewed HTTPS origin required")
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError as exc:
        raise EnrollmentDenied("reviewed HTTPS origin required") from exc
    if (parts.scheme != "https" or not parts.hostname or parts.username is not None
            or parts.password is not None or parts.path not in ("", "/")
            or parts.query or parts.fragment or port not in (None, 443)
            or parts.hostname.endswith(".local") or parts.hostname.endswith(".internal")):
        raise EnrollmentDenied("reviewed HTTPS origin required")
    try:
        address = ipaddress.ip_address(parts.hostname)
    except ValueError:
        if ("." not in parts.hostname or parts.hostname.endswith(".invalid")
                or not re.fullmatch(r"[a-z0-9.-]+", parts.hostname)
                or any(not label or label.startswith("-") or label.endswith("-")
                       for label in parts.hostname.split("."))):
            raise EnrollmentDenied("public reviewed DNS name required") from None
    else:
        if not address.is_global:
            raise EnrollmentDenied("public reviewed destination required")
    return f"https://{parts.hostname}"


def enroll_erpnext(
    *, login: CustomerLogin, registry: TenantRegistry, session_secret: str,
    csrf_token: str, reviewed: ReviewedConnector, schema_bytes: bytes,
    schema_sha256: str,
    label: str, endpoint: str, approved_origin: str, company: str,
    api_key: str, api_secret: str,
    vault_store: Callable[[str, str, str, str], str],
    vault_revoke: Callable[[str], None],
) -> EnrolledConnection:
    """Enroll through a protected backend; no secret enters registry or result.

    The approved origin is operator supplied, separate from the browser input.
    This step grants no read or execution authority. A governed grant is still
    required before a connector can contact the destination.
    """
    access = login.authorize(
        session_secret, required_role="administrator", mutating=True,
        csrf_token=csrf_token,
    )
    catalog = reviewed_catalog_entry(
        reviewed, schema_bytes=schema_bytes, schema_sha256=schema_sha256,
    )
    registration = discover(reviewed)
    if (registration.name != "erpnext" or catalog.name != registration.name
            or catalog.distribution != registration.distribution
            or catalog.version != registration.version
            or registration.execution_allowed is not False
            or {field.name: (field.kind, field.required, field.write_only)
                for field in catalog.fields} != {
                    "endpoint": ("https_url", True, False),
                    "api_key": ("secret", True, True),
                    "api_secret": ("secret", True, True),
                    "company": ("text", True, False),
                }):
        raise EnrollmentDenied("reviewed ERPNext catalog required")
    if _origin(endpoint) != _origin(approved_origin):
        raise EnrollmentDenied("destination differs from operator review")
    if (type(company) is not str or not 1 <= len(company) <= 128
            or any(character in company for character in "\r\n\x00")
            or type(api_key) is not str or not 1 <= len(api_key) <= 256
            or type(api_secret) is not str or not 1 <= len(api_secret) <= 256
            or any(character in api_key + api_secret for character in "\r\n\x00")):
        raise EnrollmentDenied("invalid connection configuration")
    reference = vault_store(access.tenant_id, registration.name, api_key, api_secret)
    try:
        with registry._connection() as db:
            reused = db.execute(
                "SELECT 1 FROM credential_refs WHERE credential_ref=? UNION "
                "SELECT 1 FROM retired_credential_refs WHERE credential_ref=?",
                (reference, reference),
            ).fetchone()
    except BaseException:
        vault_revoke(reference)
        raise
    if reused is not None:
        # A vault must create a fresh reference. Never revoke a reference that
        # might still name an existing tenant's active credential.
        raise EnrollmentDenied("vault reference reused")
    try:
        registry.enroll_credential_reference(
            # The session was checked above; resolve the same verified identity
            # from the registry without returning credentials to application state.
            login._verified_session_identity(session_secret), tenant_id=access.tenant_id,
            connector=registration, label=label, credential_ref=reference,
        )
    except BaseException:
        vault_revoke(reference)
        raise
    return EnrolledConnection(
        access.tenant_id, registration.name, _origin(endpoint), company, reference,
    )
