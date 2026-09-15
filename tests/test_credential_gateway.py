"""Credential use is after live independent custody, with one fixed native route."""

import hashlib
import io

import pytest

from orion.pilot.broker_contract import digest
from orion.pilot.gateway import CredentialGateway, response_length
from orion.pilot.journal import JournalDenied


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    certificate = tmp_path / "certificate"
    certificate.write_bytes(b"synthetic-trust-only")
    certificate.chmod(0o600)
    configs = [
        {"operation": op, "limits": {"response_bytes": 65536}} for op in ("metadata", "read")
    ]
    calls = []

    def client(action, value):
        calls.append((action, value))
        return {"authorized": True, "binding": value["binding"]}

    owner = CredentialGateway(
        configs,
        client,
        "SyntheticOnlyCredential0123456789",
        str(certificate),
        hashlib.sha256(certificate.read_bytes()).hexdigest(),
        "192.0.2.2",
    )
    owner.read = lambda path, **kw: calls.append(("source", path)) or b"{}"
    return owner, calls, configs, certificate


@pytest.mark.parametrize(
    "role,action",
    (
        ("broker", "acquire"),
        ("reasoner", "acquire"),
        ("acquisition", "issue"),
        ("owner", "acquire"),
    ),
)
def test_gateway_unauthorized_role_denies_before_custody_or_source(gateway, role, action):
    owner, calls, configs, _ = gateway
    with pytest.raises(JournalDenied):
        owner.dispatch(role, action, {"binding": digest(configs[1]), "receipt": "forged"})
    assert calls == []


@pytest.mark.parametrize("extra", ("url", "headers", "proxy", "port", "body", "redirect"))
def test_gateway_route_and_proxy_selectors_denied_before_source(gateway, extra):
    owner, calls, configs, _ = gateway
    with pytest.raises(ValueError):
        owner.dispatch(
            "acquisition",
            "acquire",
            {"binding": digest(configs[1]), "receipt": "forged", extra: "unapproved"},
        )
    assert calls == []


def test_gateway_custody_denial_or_wrong_scope_has_no_source_io(gateway):
    owner, calls, configs, _ = gateway
    value = {"binding": digest(configs[1]), "receipt": "forged"}

    def unavailable(*args):
        raise JournalDenied("unavailable")

    owner.client = unavailable
    with pytest.raises(JournalDenied):
        owner.dispatch("acquisition", "acquire", value)
    owner.client = lambda *args: {"authorized": True, "binding": "other"}
    with pytest.raises(JournalDenied):
        owner.dispatch("acquisition", "acquire", value)
    assert calls == []


def test_gateway_approval_precedes_fixed_source_route(gateway):
    owner, calls, configs, _ = gateway
    value = {"binding": digest(configs[1]), "receipt": "one-use-test-receipt"}
    assert owner.dispatch("acquisition", "acquire", value) == {"body": b"{}".hex()}
    assert calls == [("redeem", value), ("source", "/records")]


def test_gateway_changed_trust_denies_before_custody_or_source(gateway):
    owner, calls, configs, certificate = gateway
    certificate.write_bytes(b"changed")
    with pytest.raises(JournalDenied):
        owner.dispatch(
            "acquisition", "acquire", {"binding": digest(configs[0]), "receipt": "forged"}
        )
    assert calls == []


@pytest.mark.parametrize(
    "headers",
    (
        [],
        [("Content-Length", "2"), ("Content-Length", "2")],
        [("Content-Length", "999999")],
        [("Content-Length", "2"), ("Location", "/other")],
        [("Content-Length", "2"), ("Transfer-Encoding", "chunked")],
        [("Content-Length", "2"), ("Content-Encoding", "gzip")],
    ),
)
def test_gateway_strict_native_framing_matches_tls_owner(headers):
    with pytest.raises(ValueError):
        response_length(200, headers, 1024)


def test_native_transport_uses_no_credential_argv_proxy_redirect_or_url_selector(
    gateway, monkeypatch
):
    import orion.pilot.gateway as module

    owner, _, _, _ = gateway
    argv_seen, credential_pipe = [], []

    class Input(io.BytesIO):
        def close(self):
            credential_pipe.append(self.getvalue())
            super().close()

    class Native:
        stdin = Input()
        stdout = io.BytesIO(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")

        def wait(self, timeout):
            return 0

        def poll(self):
            return 0

    def spawn(argv, **kwargs):
        argv_seen.extend(argv)
        assert kwargs["close_fds"] is True
        assert not any("proxy" in k.lower() for k in kwargs["env"])
        return Native()

    monkeypatch.setattr(module.subprocess, "Popen", spawn)
    assert CredentialGateway.read(owner, "/records") == b"{}"
    assert owner.credential not in " ".join(argv_seen)
    assert credential_pipe == [
        ('header = "Authorization: Bearer ' + owner.credential + '"\n').encode()
    ]
    assert argv_seen[:4] == ["/usr/bin/curl", "--disable", "--config", "-"]
    assert argv_seen[-1] == "https://192.0.2.2:44443/records"
    assert "--location" not in argv_seen and "--insecure" not in argv_seen
    assert argv_seen[argv_seen.index("--proxy") + 1] == ""
    assert argv_seen[argv_seen.index("--max-redirs") + 1] == "0"
