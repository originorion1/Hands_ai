"""No-network token endpoint contract for the reviewed OIDC client."""

from email.message import Message
from urllib.parse import parse_qs

import pytest

from deployment.customer.oidc_client import (
    OIDCTokenExchangeDenied,
    ReviewedOIDCTokenExchange,
)

ISSUER = "https://idp.synthetic.invalid/tenant"
TOKEN = "https://idp.synthetic.invalid/oauth/token"
CALLBACK = "https://command.synthetic.invalid/callback"


class Response:
    status = 200

    def __init__(self, url=TOKEN, body=b'{"id_token":"synthetic-signed-id-token-1234567890"}'):
        self.url = url
        self.body = body
        self.headers = Message()
        self.headers["Content-Type"] = "application/json"

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def geturl(self):
        return self.url

    def read(self, limit):
        return self.body[:limit]


def test_reviewed_token_exchange_uses_post_pkce_and_vault_reference():
    calls = []

    def open_request(request, *, timeout):
        calls.append((request, timeout))
        return Response()

    client = ReviewedOIDCTokenExchange(
        issuer=ISSUER, token_endpoint=TOKEN, client_id="orion-customer",
        redirect_uri=CALLBACK, client_secret_reference="vault:synthetic-idp",
        resolve_secret=lambda reference: "private-secret" if reference == "vault:synthetic-idp" else "",
        opener=open_request,
    )
    assertion = client("one-use-code", "v" * 43, CALLBACK)
    assert assertion == "synthetic-signed-id-token-1234567890"
    request, timeout = calls[0]
    assert request.get_method() == "POST" and timeout == 10
    assert request.full_url == TOKEN
    assert parse_qs(request.data.decode()) == {
        "grant_type": ["authorization_code"], "code": ["one-use-code"],
        "code_verifier": ["v" * 43], "redirect_uri": [CALLBACK],
        "client_id": ["orion-customer"], "client_secret": ["private-secret"],
    }
    assert "private-secret" not in request.full_url
    with pytest.raises(OIDCTokenExchangeDenied):
        client("one-use-code", "short", CALLBACK)
    with pytest.raises(OIDCTokenExchangeDenied):
        client("one-use-code", "v" * 43, "https://attacker.invalid/callback")


def test_token_exchange_denies_redirect_duplicate_and_unreviewed_endpoint():
    with pytest.raises(ValueError):
        ReviewedOIDCTokenExchange(
            issuer=ISSUER, token_endpoint="https://other.invalid/token",
            client_id="orion-customer", redirect_uri=CALLBACK,
        )
    for response in (
        Response(url="https://other.invalid/token"),
        Response(body=b'{"id_token":"one","id_token":"two"}'),
        Response(body=b'{"access_token":"present"}'),
    ):
        client = ReviewedOIDCTokenExchange(
            issuer=ISSUER, token_endpoint=TOKEN, client_id="orion-customer",
            redirect_uri=CALLBACK, opener=lambda *_args, reply=response, **_kwargs: reply,
        )
        with pytest.raises(OIDCTokenExchangeDenied):
            client("one-use-code", "v" * 43, CALLBACK)
