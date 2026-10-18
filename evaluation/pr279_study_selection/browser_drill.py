"""Real Chromium drill against an in-process synthetic OIDC and portal transport."""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key
from playwright.sync_api import sync_playwright

from deployment.customer.login import CustomerLogin
from deployment.customer.oidc import OIDCVerifier, VerifiedIdentity
from deployment.customer.portal_http import CustomerPortalHTTP
from deployment.customer.service import CustomerPortalService, CustomerReportBinding
from deployment.customer.tenants import TenantRegistry
from deployment.intelligence.procurement_report import (
    ProcurementReportSource,
    persist_procurement_finding,
)
from orion.presentation.report_interface import BoundReportSource

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tests"))
from test_procurement_price_270 import _admitted_run

ISSUER = "https://idp.example.invalid/tenant"
PORTAL = "https://command.example.invalid"
TENANT = "synthetic-procurement-tenant"


def _bootstrap(registry, signer, tenant, serial, subject):
    approval = {
        "approval_id": f"synthetic-browser-approval-{serial:04d}", "tenant_id": tenant,
        "organization_name": f"Synthetic {tenant}", "country": "US", "timezone": "UTC",
        "sector": "testing", "data_residency": "approved-region", "retention_days": 30,
        "admin_issuer": ISSUER, "admin_subject": subject, "expires_at": int(time.time()) + 300,
    }
    message = b"ORION CUSTOMER BOOTSTRAP V1\0" + json.dumps(
        approval, sort_keys=True, separators=(",", ":")
    ).encode()
    signature = base64.b64encode(signer.sign(message)).decode()
    identity = VerifiedIdentity(ISSUER, subject, f"browser-{serial}", int(time.time()) + 300)
    registry.provision(identity, approval=approval, signature=signature)
    return identity


class Exchange:
    assertion = None

    def __call__(self, code, verifier, redirect):
        assert code == "one-use-browser-code" and len(verifier) >= 32
        assert redirect == f"{PORTAL}/callback"
        return self.assertion


def main() -> None:
    import jwt

    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-dir", type=Path)
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="orion-pr279-browser-") as directory:
        private = Path(directory)
        result, _ = _admitted_run(private)
        base = BoundReportSource.from_private_binding(result.binding_path)
        path, digest = persist_procurement_finding(
            base=base, target_ref="holdout_a-T", company="Synthetic Procurement Co",
        )
        source = ProcurementReportSource(base, path, digest)
        expected = source.read_view()
        signer = Ed25519PrivateKey.generate()
        registry = TenantRegistry(private / "registry.sqlite", approval_public_key=signer.public_key())
        alpha = _bootstrap(registry, signer, TENANT, 1, "browser-admin")
        beta = _bootstrap(registry, signer, "synthetic-other-tenant", 2, "other-admin")
        registry.add_member(beta, tenant_id="synthetic-other-tenant", member=alpha, role="analyst")
        key = generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        verifier = OIDCVerifier(issuer=ISSUER, audience="orion-browser", public_keys={"key-1": pem})
        exchange = Exchange()
        login = CustomerLogin(
            registry, verifier, authorization_endpoint="https://idp.example.invalid/authorize",
            client_id="orion-browser", redirect_uri=f"{PORTAL}/callback", token_exchange=exchange,
        )
        active = {"value": True}

        @contextmanager
        def commit_scope(_authorization_id):
            yield

        binding = CustomerReportBinding(
            source, "synthetic-procurement-read", lambda _id: active["value"], commit_scope,
        )
        service = CustomerPortalService(
            login, registry, report_lookup=lambda tenant: binding if tenant == TENANT else None,
        )
        app = CustomerPortalHTTP(login=login, service=service, origin=PORTAL)
        statuses = []
        blocked_external = []

        with sync_playwright() as playwright:
            executable = os.environ.get("ORION_TEST_CHROMIUM_PATH")
            browser = playwright.chromium.launch(
                headless=True, **({"executable_path": executable} if executable else {}),
            )
            context = browser.new_context(ignore_https_errors=True)

            def handle(route):
                request = route.request
                parsed = urlsplit(request.url)
                if parsed.hostname == "idp.example.invalid" and parsed.path == "/authorize":
                    query = parse_qs(parsed.query)
                    now = int(time.time())
                    exchange.assertion = jwt.encode({
                        "iss": ISSUER, "sub": "browser-admin", "aud": "orion-browser",
                        "iat": now, "exp": now + 240, "nonce": query["nonce"][0],
                    }, key, algorithm="RS256", headers={"kid": "key-1"})
                    callback = f"{PORTAL}/callback?state={query['state'][0]}&code=one-use-browser-code"
                    route.fulfill(status=200, headers={"Content-Type": "text/html"},
                                  body=f'<meta http-equiv="refresh" content="0;url={callback}">')
                    return
                if parsed.hostname != "command.example.invalid":
                    blocked_external.append(parsed.hostname)
                    route.abort()
                    return
                payload = request.post_data_buffer or b""
                response = {}

                def start(status, headers):
                    response["status"] = status
                    response["headers"] = headers

                headers = request.headers
                environ = {
                    "wsgi.url_scheme": "https", "HTTP_HOST": parsed.netloc,
                    "PATH_INFO": parsed.path, "QUERY_STRING": parsed.query,
                    "REQUEST_METHOD": request.method, "HTTP_COOKIE": headers.get("cookie", ""),
                    "HTTP_ORIGIN": headers.get("origin", ""),
                    "HTTP_X_ORION_CSRF": headers.get("x-orion-csrf", ""),
                    "CONTENT_LENGTH": str(len(payload)),
                    "CONTENT_TYPE": headers.get("content-type", ""),
                    "wsgi.input": io.BytesIO(payload),
                }
                body = b"".join(app(environ, start))
                status = int(response["status"].split()[0])
                statuses.append([parsed.path, status])
                out_headers = {}
                cookies = []
                for name, value in response["headers"]:
                    if name.lower() == "set-cookie":
                        first = value.split(";", 1)[0]
                        cookie_name, cookie_value = first.split("=", 1)
                        cookies.append({
                            "name": cookie_name, "value": cookie_value,
                            "url": PORTAL, "secure": True,
                            "httpOnly": "httponly" in value.lower(),
                            "sameSite": "Strict" if "samesite=strict" in value.lower() else "Lax",
                        })
                    else:
                        out_headers[name] = (f"{PORTAL}{value}" if name.lower() == "location"
                                             and value.startswith("/") else value)
                if cookies:
                    context.add_cookies(cookies)
                if status in {302, 303} and "Location" in out_headers:
                    destination = out_headers["Location"]
                    route.fulfill(status=200, headers={"Content-Type": "text/html"},
                                  body=f'<meta http-equiv="refresh" content="0;url={destination}">')
                    return
                route.fulfill(status=status, headers=out_headers, body=body)

            context.route("**/*", handle)
            page = context.new_page()
            page.goto(f"{PORTAL}/customer/")
            page.locator("#loginTenant").fill(TENANT)
            page.locator("#loginForm button").click()
            page.wait_for_timeout(1200)
            callback_fallback = False
            if page.url == "chrome-error://chromewebdata/":
                # The synthetic transport's redirected navigation is not
                # reliably intercepted by this older Chromium build. Complete
                # the same OIDC code flow in-process and pass only cookies to
                # the browser, recording this transport limitation below.
                start = login.start(tenant_id=TENANT)
                query = parse_qs(urlsplit(start.authorization_url).query)
                now = int(time.time())
                exchange.assertion = jwt.encode({
                    "iss": ISSUER, "sub": "browser-admin", "aud": "orion-browser",
                    "iat": now, "exp": now + 240, "nonce": query["nonce"][0],
                }, key, algorithm="RS256", headers={"kid": "key-1"})
                session = login.finish(state=query["state"][0], code="one-use-browser-code")
                callback_fallback = True
                context.add_cookies([
                    {"name": "__Host-orion_session", "value": session.cookie.split(";", 1)[0].split("=", 1)[1], "url": PORTAL, "secure": True, "httpOnly": True, "sameSite": "Lax"},
                    {"name": "__Host-orion_csrf", "value": session.csrf_token, "url": PORTAL, "secure": True, "httpOnly": False, "sameSite": "Strict"},
                ])
                page.goto(f"{PORTAL}/customer/")
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if (page.locator("#reportPanel").count()
                        and page.locator("#reportPanel").get_attribute("hidden") is None
                        and "$60.00" in page.locator("#reportDetails").inner_text()):
                    break
                time.sleep(0.1)
            else:
                raise AssertionError(f"synthetic report did not render: {statuses}; url={page.url}")
            assert expected["procurement"]["target_ref"] in page.locator("#reportDetails").inner_text()
            assert "$60.00" in page.locator("#reportDetails").inner_text()
            assert str(result.run_id) in page.locator("#reportDetails").inner_text()
            assert expected["procurement"]["source_data_sha256"] in page.locator("#reportDetails").inner_text()
            if args.evidence_dir:
                args.evidence_dir.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(args.evidence_dir / "authenticated-finding.png"), full_page=True)
                (args.evidence_dir / "persisted-report.json").write_text(
                    json.dumps(expected, indent=2, sort_keys=True) + "\n",
                )
            page.locator("#switchTenant").fill("synthetic-other-tenant")
            page.locator("#switchForm button").click()
            page.get_by_text("Synthetic synthetic-other-tenant", exact=True).wait_for()
            assert page.locator("#reportPanel").is_hidden()
            page.locator("#switchTenant").fill(TENANT)
            page.locator("#switchForm button").click()
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if (page.locator("#reportPanel").get_attribute("hidden") is None
                        and "$60.00" in page.locator("#reportDetails").inner_text()):
                    break
                time.sleep(0.1)
            else:
                raise AssertionError("synthetic report did not return after authorized switch")
            active["value"] = False
            page.evaluate("refresh().catch(() => {})")
            page.locator("#loginPanel").wait_for(state="visible")
            assert page.locator("#reportPanel").is_hidden()
            assert page.locator("#reportDetails").inner_text() == ""
            assert "Access denied" in page.locator("#status").inner_text()
            if args.evidence_dir:
                page.screenshot(path=str(args.evidence_dir / "revoked-cleared.png"), full_page=True)
            assert not blocked_external
            print(json.dumps({
                "browser": browser.version, "synthetic_transport": True,
                "callback_transport_fallback": callback_fallback,
                "tenant_count": 2, "run_id": str(result.run_id),
                "source_sha256": expected["procurement"]["source_data_sha256"],
                "finding_sha256": expected["procurement"]["finding_sha256"],
                "estimated_exposure_usd": expected["procurement"]["estimated_exposure_usd"],
                "report_visible": True, "tenant_switch_clears_report": True,
                "revocation_clears_report": True, "http_statuses": statuses,
            }, sort_keys=True))
            browser.close()


if __name__ == "__main__":
    main()
