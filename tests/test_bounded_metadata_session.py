"""Offline exact-boundary tests for the installed metadata-only launch path."""

import json
from contextlib import nullcontext
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from deployment.customer.bounded_metadata import (
    MetadataGrant,
    MetadataSessionDenied,
    purge_expired_metadata_state,
    run_bounded_metadata_session,
)
from deployment.customer.metadata_approval import (
    ApprovedMetadataGrantLedger,
    approval_packet,
)
from deployment.customer.metadata_study_report import (
    MetadataStudyReportSource,
    persist_metadata_study,
)
from deployment.customer.portal import AuthorizationProposal
from orion.discovery.credential_references import CredentialEnvironmentReferences

START = datetime(2026, 10, 18, 10, tzinfo=UTC)
ENV = {"SYNTHETIC_KEY_REF": "synthetic-key", "SYNTHETIC_SECRET_REF": "synthetic-secret"}


class Response:
    def __init__(self, url, payload):
        self.url = url
        self.body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def geturl(self):
        return self.url

    def read(self, size):
        return self.body[:size]


def grant(count=2, **changes):
    names = tuple(f"Doc {index:02d}" for index in range(count))
    value = MetadataGrant(
        authorization_id="synthetic-metadata-grant", tenant_id="synthetic-tenant",
        external_customer_reference="synthetic-external-reference", company="Synthetic Co",
        origin="https://synthetic.invalid",
        credential_references=CredentialEnvironmentReferences(
            "SYNTHETIC_KEY_REF", "SYNTHETIC_SECRET_REF"
        ),
        fields_by_doctype=tuple((name, ("next",)) for name in names),
        seed_doctype=names[0], starts_at=START,
        expires_at=START + timedelta(minutes=30),
    )
    return replace(value, **changes)


def transport_for(names, calls, *, after=None):
    def transport(request, *, timeout):
        assert request.get_method() == "GET"
        assert timeout == 20
        assert urlsplit(request.full_url).path == (
            "/api/method/frappe.desk.form.load.getdoctype"
        )
        name = parse_qs(urlsplit(request.full_url).query)["doctype"][0]
        calls.append(name)
        if after is not None:
            after()
        index = names.index(name)
        target = names[index + 1] if index + 1 < len(names) else "Outside Scope"
        return Response(request.full_url, {"message": {"docs": [{
            "name": name, "fields": [{
                "fieldname": "next", "fieldtype": "Link", "options": target,
                "label": "RAW LABEL MUST NOT PERSIST", "default": "RAW VALUE MUST NOT PERSIST",
            }, {"fieldname": "payroll_secret", "fieldtype": "Password",
                "default": "SENSITIVE MUST NOT PERSIST"}],
        }]}})
    return transport


def run(tmp_path, value, transport, *, lookup=None, clock=None, monotonic=None):
    parent = tmp_path / "private"
    parent.mkdir(mode=0o700, exist_ok=True)
    return run_bounded_metadata_session(
        value, lookup=lookup or (lambda _: value), environment=ENV,
        transport=transport, commit_scope=lambda _: nullcontext(),
        state_directory=parent / "session",
        clock=clock or (lambda: START), monotonic=monotonic or (lambda: 0.0),
    )


def test_25_attempts_are_allowed_but_request_26_is_denied_before_transport(tmp_path):
    value = grant(26)
    calls = []
    with pytest.raises(MetadataSessionDenied, match="ceiling exhausted"):
        run(tmp_path, value, transport_for(tuple(dict(value.fields_by_doctype)), calls))
    assert len(calls) == 25
    state = tmp_path / "private/session"
    assert len(list(state.glob("attempt-*.json"))) == 25
    assert not (state / "report.json").exists()
    assert b"SENSITIVE MUST NOT PERSIST" not in b"".join(
        path.read_bytes() for path in state.iterdir() if path.is_file()
    )


@pytest.mark.parametrize("revoke_during", [False, True])
def test_signed_proposal_binding_and_durable_metadata_revocation(tmp_path, revoke_during):
    value = grant(2)
    proposal = AuthorizationProposal(
        tenant_id=value.tenant_id, companies=(value.company,),
        fields_by_doctype=value.fields_by_doctype,
        sensitive_exclusions=("payroll", "personal records", "attachments"),
        historical_start=START.date(), historical_end=START.date(),
        max_requests=25, max_duration_seconds=1800,
        max_evidence_bytes=value.max_evidence_bytes, retention_days=1,
        revocation_operator="Synthetic reviewer",
    ).review_packet()
    packet = approval_packet(proposal, value, proposal_id="reviewed-synthetic-proposal")
    signer = Ed25519PrivateKey.generate()
    import json as _json

    signature = signer.sign(
        b"ORION METADATA APPROVAL V1\0" + _json.dumps(
            packet, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode()
    )
    directory = tmp_path / "approvals"
    directory.mkdir(mode=0o700)
    ledger_path = directory / "ledger.sqlite"
    ledger = ApprovedMetadataGrantLedger(
        ledger_path, grant=value, proposal=proposal, packet=packet,
        signature=signature, approval_key=signer.public_key(),
    )
    ledger.claim()
    calls = []
    parent = tmp_path / "run"
    parent.mkdir(mode=0o700)
    transport = transport_for(
        tuple(dict(value.fields_by_doctype)), calls,
        after=ledger.revoke if revoke_during else None,
    )
    if revoke_during:
        with pytest.raises(MetadataSessionDenied, match="revoked"):
            run_bounded_metadata_session(
                value, lookup=ledger.lookup, environment=ENV,
                transport=transport, commit_scope=ledger.commit_scope,
                state_directory=parent / "session",
                clock=lambda: START, monotonic=lambda: 0.0,
            )
        ledger.finish(complete=False)
        assert calls == ["Doc 00"]
        assert not (parent / "session/evidence-01.json").exists()
    else:
        result = run_bounded_metadata_session(
            value, lookup=ledger.lookup, environment=ENV,
            transport=transport, commit_scope=ledger.commit_scope,
            state_directory=parent / "session",
            clock=lambda: START, monotonic=lambda: 0.0,
        )
        assert result.status == "complete" and calls == ["Doc 00", "Doc 01"]
        ledger.finish(complete=True)
    ledger.revoke()
    reopened = ApprovedMetadataGrantLedger(
        ledger_path, grant=value, proposal=proposal, packet=packet,
        signature=signature, approval_key=signer.public_key(), existing=True,
    )
    assert reopened.lookup(value.authorization_id) is None
    with (pytest.raises(MetadataSessionDenied, match="revoked"),
          reopened.commit_scope(value.authorization_id)):
        pass
    narrow = proposal | {"max_requests": 1}
    from deployment.customer.portal import authorization_scope_digest

    scope = {key: item for key, item in narrow.items() if key not in {
        "kind", "status", "execution_authority", "scope_sha256",
    }}
    narrow["scope_sha256"] = authorization_scope_digest(scope)
    with pytest.raises(MetadataSessionDenied, match="exceeds reviewed proposal"):
        approval_packet(narrow, value, proposal_id="reviewed")
    with pytest.raises(MetadataSessionDenied, match="exceeds reviewed proposal"):
        approval_packet(proposal, replace(value, retention_days=2),
                        proposal_id="reviewed")


def test_exact_25_request_completion_and_projected_evidence(tmp_path):
    value = grant(25)
    calls = []
    result = run(tmp_path, value, transport_for(tuple(dict(value.fields_by_doctype)), calls))
    assert result.status == "complete" and result.attempted_requests == 25
    assert len(result.evidence_digests) == 25
    assert b"RAW LABEL MUST NOT PERSIST" not in (
        tmp_path / "private/session/evidence-01.json"
    ).read_bytes()
    assert len(calls) == 25


def test_approved_retention_denies_early_or_wrong_scope_and_purges_at_boundary(tmp_path):
    value = grant(2)
    run(tmp_path, value, transport_for(tuple(dict(value.fields_by_doctype)), []))
    state = tmp_path / "private/session"
    deadline = value.expires_at + timedelta(days=value.retention_days)
    with pytest.raises(MetadataSessionDenied, match="retention period"):
        purge_expired_metadata_state(value, state_directory=state,
                                     now=deadline - timedelta(microseconds=1))
    with pytest.raises(MetadataSessionDenied, match="scope mismatch"):
        purge_expired_metadata_state(replace(value, authorization_id="foreign"),
                                     state_directory=state, now=deadline)
    extra = state / "unreviewed.json"
    extra.write_text("do not delete")
    with pytest.raises(MetadataSessionDenied, match="unreviewed"):
        purge_expired_metadata_state(value, state_directory=state, now=deadline)
    extra.unlink()
    assert purge_expired_metadata_state(value, state_directory=state, now=deadline) == 6
    assert not state.exists()


def test_completed_metadata_study_is_same_source_and_invalidates_on_tamper(tmp_path):
    value = grant(2)
    run(tmp_path, value, transport_for(tuple(dict(value.fields_by_doctype)), []))
    state = tmp_path / "private/session"
    path, digest = persist_metadata_study(grant=value, state_directory=state)
    source = MetadataStudyReportSource(value, state, digest)
    view = source.read_view()
    assert view["lineage"]["tenant_id"] == value.tenant_id
    assert view["lineage"]["authorization_id"] == value.authorization_id
    assert len(view["lineage"]["evidence_ids"]) == 2
    assert view["study"]["observed_links"][0]["source"] == "Doc 00"
    assert view["study"]["selected_question"] == (
        "Does Doc 00.next relate to Doc 01 in permitted records?"
    )
    assert view["study"]["status"] == "proposal_only"
    assert view["authority"] == {"execution_allowed": False, "erp_writes": 0}
    assert path.read_bytes().endswith(b"\n")
    evidence = state / "evidence-01.json"
    evidence.write_bytes(evidence.read_bytes().replace(b"Doc 01", b"Doc 09"))
    with pytest.raises(MetadataSessionDenied, match="digest mismatch"):
        source.read_view()


def test_metadata_study_abstains_without_permitted_link_and_retention_purges_it(tmp_path):
    value = grant(1)
    run(tmp_path, value, transport_for(("Doc 00",), []))
    state = tmp_path / "private/session"
    _, digest = persist_metadata_study(grant=value, state_directory=state)
    view = MetadataStudyReportSource(value, state, digest).read_view()
    assert view["study"]["selected_question"] is None
    assert view["study"]["status"] == "abstained"
    assert purge_expired_metadata_state(
        value, state_directory=state,
        now=value.expires_at + timedelta(days=value.retention_days),
    ) == 5


def test_revocation_after_first_response_denies_persistence_and_restart(tmp_path):
    value = grant()
    active = [True]
    calls = []
    def after():
        active[0] = False
    with pytest.raises(MetadataSessionDenied, match="revoked"):
        run(tmp_path, value, transport_for(tuple(dict(value.fields_by_doctype)), calls,
                                       after=after),
            lookup=lambda _: value if active[0] else None)
    assert calls == ["Doc 00"]
    assert not (tmp_path / "private/session/evidence-01.json").exists()
    with pytest.raises(MetadataSessionDenied, match="durable state"):
        run(tmp_path, value, transport_for(tuple(dict(value.fields_by_doctype)), []))


def test_expiry_at_boundary_denies_second_request(tmp_path):
    value = grant()
    now = [START]
    calls = []
    def advance():
        # First response remains valid; expiry is reached before the next attempt.
        now[0] = START + timedelta(minutes=30)
    with pytest.raises(MetadataSessionDenied, match="expired"):
        run(tmp_path, value, transport_for(tuple(dict(value.fields_by_doctype)), calls,
                                       after=advance), clock=lambda: now[0])
    assert calls == ["Doc 00"]


def test_elapsed_30_minute_limit_denies_even_if_wall_clock_stalls(tmp_path):
    value = grant()
    elapsed = [0.0]
    calls = []
    def advance():
        elapsed[0] = 1800.0
    with pytest.raises(MetadataSessionDenied, match="expired"):
        run(tmp_path, value, transport_for(tuple(dict(value.fields_by_doctype)), calls,
                                       after=advance), monotonic=lambda: elapsed[0])
    assert calls == ["Doc 00"]
    assert not (tmp_path / "private/session/evidence-01.json").exists()


def test_partial_transport_failure_keeps_attempt_and_denies_restart(tmp_path):
    value = grant()
    def broken(_request, *, timeout):
        assert timeout == 20
        raise OSError("synthetic interruption")
    with pytest.raises(OSError):
        run(tmp_path, value, broken)
    assert (tmp_path / "private/session/attempt-01.json").exists()
    with pytest.raises(MetadataSessionDenied, match="durable state"):
        run(tmp_path, value, broken)


def test_redirect_is_denied_after_durable_attempt(tmp_path):
    value = grant(1)
    def redirect(request, *, timeout):
        assert timeout == 20
        response = transport_for(("Doc 00",), [])(request, timeout=timeout)
        response.url = "https://unreviewed.invalid/redirect"
        return response
    with pytest.raises(MetadataSessionDenied, match="redirect"):
        run(tmp_path, value, redirect)
    assert (tmp_path / "private/session/attempt-01.json").exists()
    assert not (tmp_path / "private/session/evidence-01.json").exists()


def test_sensitive_scope_and_oversized_evidence_are_denied(tmp_path):
    with pytest.raises(MetadataSessionDenied, match="exact identity"):
        grant(tenant_id="Terrace-Beirut")
    with pytest.raises(MetadataSessionDenied, match="sensitive field"):
        grant(fields_by_doctype=(("Doc 00", ("payroll_secret",)),), seed_doctype="Doc 00")
    value = grant(1, max_evidence_bytes=1)
    calls = []
    with pytest.raises(MetadataSessionDenied, match="evidence exceeds"):
        run(tmp_path, value, transport_for(("Doc 00",), calls))
    assert len(calls) == 1
    assert not list((tmp_path / "private/session").glob("evidence-*.json"))
