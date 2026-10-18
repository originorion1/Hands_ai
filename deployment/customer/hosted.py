"""Compose the reviewed customer WSGI portal for a separate stateful host.

The host supplies a private registry, vault callbacks, and a tenant-indexed
report lookup. This module opens no listener, creates no grant, and never
contacts an ERP system. The public HTTPS reverse proxy must preserve the exact
Host and scheme; CustomerPortalHTTP denies anything else.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from orion_connector_sdk import ReviewedConnector

from .auth0_provider import ReviewedAuth0Provider
from .google_secret_vault import GoogleSecretManagerVault
from .login import CustomerLogin
from .oidc import OIDCVerifier
from .oidc_client import OIDCTokenExchangeDenied, ReviewedOIDCTokenExchange
from .portal_http import CustomerPortalHTTP
from .service import CustomerPortalService, CustomerReportBinding
from .tenants import TenantRegistry


def create_hosted_customer_portal(
    *,
    origin: str,
    registry: TenantRegistry,
    oidc_issuer: str,
    oidc_authorization_endpoint: str,
    oidc_token_endpoint: str,
    oidc_client_id: str,
    oidc_redirect_uri: str,
    oidc_public_keys: Mapping[str, bytes],
    oidc_client_secret_reference: str,
    vault_read_client_secret: Callable[[str], str],
    vault_store_connector: Callable[[str, str, str, str], str],
    vault_revoke_connector: Callable[[str], None],
    report_lookup: Callable[[str], CustomerReportBinding | None],
    reviewed_connector: ReviewedConnector,
    connector_schema_bytes: bytes,
    connector_schema_sha256: str,
    approved_erp_origin: str,
) -> CustomerPortalHTTP:
    """Return one WSGI app with all customer authorities injected server-side.

    The token client always starts with system TLS, no proxy and no redirects.
    Neither vault callbacks nor the report lookup may treat a browser-selected
    tenant as authority without the checks in CustomerPortalService.
    """
    if (
        not isinstance(registry, TenantRegistry)
        or not callable(vault_read_client_secret)
        or not callable(vault_store_connector)
        or not callable(vault_revoke_connector)
        or not callable(report_lookup)
        or not isinstance(reviewed_connector, ReviewedConnector)
        or type(connector_schema_bytes) is not bytes
        or type(connector_schema_sha256) is not str
        or type(approved_erp_origin) is not str
        or type(oidc_client_secret_reference) is not str
        or not oidc_client_secret_reference.startswith("vault:")
    ):
        raise ValueError("reviewed hosted portal dependencies required")
    verifier = OIDCVerifier(
        issuer=oidc_issuer, audience=oidc_client_id,
        public_keys=oidc_public_keys,
    )

    def read_client_secret(reference: str) -> str:
        try:
            return vault_read_client_secret(reference)
        except Exception:  # noqa: BLE001 - external vault messages may contain secrets
            raise OIDCTokenExchangeDenied("client credential unavailable") from None

    def store_connector(tenant: str, connector: str, key: str, secret: str) -> str:
        try:
            return vault_store_connector(tenant, connector, key, secret)
        except Exception:  # noqa: BLE001 - external vault messages may contain secrets
            raise RuntimeError("connector vault unavailable") from None

    def revoke_connector(reference: str) -> None:
        try:
            vault_revoke_connector(reference)
        except Exception:  # noqa: BLE001 - external vault messages may contain secrets
            raise RuntimeError("connector vault unavailable") from None

    exchange = ReviewedOIDCTokenExchange(
        issuer=oidc_issuer, token_endpoint=oidc_token_endpoint,
        client_id=oidc_client_id, redirect_uri=oidc_redirect_uri,
        client_secret_reference=oidc_client_secret_reference,
        resolve_secret=read_client_secret,
    )
    login = CustomerLogin(
        registry, verifier,
        authorization_endpoint=oidc_authorization_endpoint,
        client_id=oidc_client_id, redirect_uri=oidc_redirect_uri,
        token_exchange=exchange,
    )
    service = CustomerPortalService(login, registry, report_lookup)
    return CustomerPortalHTTP(
        login=login, service=service, origin=origin,
        reviewed_connector=reviewed_connector,
        schema_bytes=connector_schema_bytes,
        schema_sha256=connector_schema_sha256,
        approved_erp_origin=approved_erp_origin,
        vault_store=store_connector,
        vault_revoke=revoke_connector,
    )


def create_auth0_google_portal(
    *, origin: str, registry: TenantRegistry, provider: ReviewedAuth0Provider,
    oidc_client_id: str, oidc_client_secret_reference: str,
    vault: GoogleSecretManagerVault,
    report_lookup: Callable[[str], CustomerReportBinding | None],
    reviewed_connector: ReviewedConnector, connector_schema_bytes: bytes,
    connector_schema_sha256: str, approved_erp_origin: str,
) -> CustomerPortalHTTP:
    """Wire reviewed Auth0 metadata and private Google vault callbacks only."""
    if not isinstance(provider, ReviewedAuth0Provider) or not isinstance(
        vault, GoogleSecretManagerVault
    ):
        raise TypeError("reviewed provider adapters required")
    return create_hosted_customer_portal(
        origin=origin, registry=registry,
        oidc_issuer=provider.issuer,
        oidc_authorization_endpoint=provider.authorization_endpoint,
        oidc_token_endpoint=provider.token_endpoint,
        oidc_client_id=oidc_client_id,
        oidc_redirect_uri=f"{origin}/callback",
        oidc_public_keys=provider.public_keys,
        oidc_client_secret_reference=oidc_client_secret_reference,
        vault_read_client_secret=vault.read_client_secret,
        vault_store_connector=vault.store_connector,
        vault_revoke_connector=vault.revoke_connector,
        report_lookup=report_lookup,
        reviewed_connector=reviewed_connector,
        connector_schema_bytes=connector_schema_bytes,
        connector_schema_sha256=connector_schema_sha256,
        approved_erp_origin=approved_erp_origin,
    )
