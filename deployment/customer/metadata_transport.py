"""Reviewed direct HTTPS transport for a metadata-only ERPNext grant.

The transport denies proxies and redirects before credentials can be sent to
another destination. Network egress and DNS policy remain deployment gates.
"""

from __future__ import annotations

import ssl
from dataclasses import dataclass, field
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)

from orion.discovery.erpnext_adapter import _normalize_base_url, _validate_resource

from .bounded_metadata import MetadataSessionDenied

_PATH = "/api/method/frappe.desk.form.load.getdoctype"


class _DenyRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        del request, fp, code, msg, headers, newurl


@dataclass(frozen=True, slots=True)
class ReviewedMetadataTransport:
    """Direct TLS-verifying GET transport bound to one reviewed HTTPS origin."""

    origin: str
    ca_bundle: Path | None = field(default=None, repr=False)
    _opener: object = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if _normalize_base_url(self.origin) != self.origin:
            raise MetadataSessionDenied("exact reviewed HTTPS origin required")
        if self.ca_bundle is not None and (
            not isinstance(self.ca_bundle, Path) or not self.ca_bundle.is_absolute()
            or not self.ca_bundle.is_file() or self.ca_bundle.is_symlink()
        ):
            raise MetadataSessionDenied("reviewed CA bundle path required")
        context = ssl.create_default_context(
            cafile=str(self.ca_bundle) if self.ca_bundle is not None else None,
        )
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        object.__setattr__(self, "_opener", build_opener(
            ProxyHandler({}), HTTPSHandler(context=context), _DenyRedirect(),
        ))

    def __call__(self, request: Request, *, timeout: int):
        if not isinstance(request, Request) or request.get_method() != "GET" or timeout != 20:
            raise MetadataSessionDenied("metadata transport permits one bounded GET")
        parsed = urlsplit(request.full_url)
        if (f"{parsed.scheme}://{parsed.netloc}" != self.origin
                or parsed.path != _PATH or parsed.fragment):
            raise MetadataSessionDenied("metadata destination or path denied")
        query = parse_qs(parsed.query, keep_blank_values=True)
        if set(query) != {"doctype"} or len(query["doctype"]) != 1:
            raise MetadataSessionDenied("metadata query scope denied")
        try:
            _validate_resource(query["doctype"][0])
            return self._opener.open(request, timeout=timeout)
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as error:
            raise MetadataSessionDenied("reviewed metadata transport failed") from error
