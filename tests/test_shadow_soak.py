import json
from dataclasses import asdict
from datetime import UTC, datetime
from io import StringIO
from urllib.parse import parse_qs, urlparse
from uuid import UUID

import pytest

from orion.contracts import Evidence, EvidenceKind, Observation
from orion.discovery.erpnext_study_router import run_erpnext_governed_study
from orion.learning.autonomous_loop import (
    AuthorizationEnvelope,
    LearningObjective,
    StudyOutcome,
)
from orion.learning.shadow_soak import (
    ShadowSoakSessionEnvelope,
    ShadowSoakStopReason,
    run_autonomous_shadow_soak,
)
from orion.learning.study_capability import (
    StudyCapability,
    run_routed_governed_record_evidence,
)
from orion.observability.activity import ActivityEvent, ActivityEventType
from orion.observability.demo import run_demo as run_watcher_demo
from orion.observability.terminal import TerminalActivityWatcher, render_activity_event
from orion.stores.sqlite_historical_evidence import SQLiteHistoricalEvidenceStore
from orion.understanding.metadata import (
    MetadataUnderstanding,
    StructuralEntity,
    StructuralField,
)

TENANT = "synthetic-tenant"
OBJECTIVE = LearningObjective("objective", "bounded synthetic soak")
START = datetime(2026, 9, 5, tzinfo=UTC)


def field(entity, name, *, required=False):
    return StructuralField(
        entity,
        name,
        "Data",
        None,
        None,
        required,
        False,
        False,
        False,
    )


def entity(name, *, child=False, single=False, submittable=False, hidden=False):
    selected = StructuralField(
        name,
        "selected",
        "Data",
        None,
        None,
        True,
        False,
        hidden,
        False,
    )
    return StructuralEntity(
        name,
        None,
        submittable,
        child,
        single,
        (selected, field(name, "company")),
        (),
    )


def understanding(*names, **entity_options):
    return MetadataUnderstanding(
        TENANT,
        tuple(entity(name, **entity_options) for name in names),
    )


def authorization(*names, cycles=10, per_study=1, cumulative=10):
    return AuthorizationEnvelope(
        TENANT,
        OBJECTIVE.objective_id,
        allowed_record_entities=frozenset(names),
        allowed_record_fields=tuple((name, ("selected",)) for name in names),
        max_cycles=cycles,
        max_records_per_proposal=per_study,
        max_cumulative_records=cumulative,
    )


def session(
    *names,
    cycles=10,
    reads=10,
    per_study=1,
    cumulative=10,
    failures=3,
    seconds=60,
):
    return ShadowSoakSessionEnvelope(
        authorization(*names, cycles=cycles, per_study=per_study, cumulative=cumulative),
        max_wall_clock_seconds=seconds,
        max_study_cycles=cycles,
        max_erp_reads=reads,
        max_observations_per_study=per_study,
        max_cumulative_observations=cumulative,
        max_consecutive_non_progress=failures,
    )


def observation(resource, value, *, tenant=TENANT, index=1):
    return Observation(
        Evidence(
            kind=EvidenceKind.API,
            source="synthetic-reader",
            tenant_id=tenant,
            observed_at=START,
            payload={
                "resource": resource,
                "record": {
                    "name": f"record-{index}",
                    "company": "private-company-value",
                    "selected": value,
                },
            },
        )
    )


class CountingStore:
    def __init__(self, path):
        self.store = SQLiteHistoricalEvidenceStore(path)
        self.resource_lists = 0
        self.loads = 0
        self.appends = 0

    def list_resources(self, *, tenant_id):
        self.resource_lists += 1
        return self.store.list_resources(tenant_id=tenant_id)

    def load_all(self, *, tenant_id, resource):
        self.loads += 1
        return self.store.load_all(tenant_id=tenant_id, resource=resource)

    def append(self, batch):
        self.appends += 1
        return self.store.append(batch)


def run(model, limits, store, reader=None, *, study_runner=None, **kwargs):
    if study_runner is None:
        def study_runner(request, evidence_sink, permit_read):
            permit_read()
            return run_routed_governed_record_evidence(
                request,
                envelope=limits.authorization,
                understanding=model,
                readers={
                    StudyCapability.ORDINARY_RECORD: reader,
                    StudyCapability.SUBMITTED_DOCUMENT: reader,
                },
                evidence_sink=evidence_sink,
            )

    return run_autonomous_shadow_soak(
        OBJECTIVE,
        model,
        limits,
        store=store,
        study_runner=study_runner,
        clock=kwargs.pop("clock", lambda: START),
        monotonic=kwargs.pop("monotonic", lambda: 0.0),
        **kwargs,
    )


def test_successful_cycles_reload_reselect_append_and_verify_once(tmp_path):
    alpha = entity("Alpha")
    alpha = StructuralEntity(
        alpha.doctype,
        alpha.module,
        alpha.is_submittable,
        alpha.is_child_table,
        alpha.is_single,
        alpha.fields
        + (
            StructuralField(
                "Alpha",
                "related",
                "Link",
                None,
                "Beta",
                False,
                True,
                False,
                False,
            ),
        ),
        alpha.provenance_ids,
    )
    model = MetadataUnderstanding(TENANT, (alpha, entity("Beta")))
    store = CountingStore(tmp_path / "evidence.sqlite3")
    calls = []

    def reader(resource, fields, bound):
        calls.append((resource, fields, bound))
        return (observation(resource, f"private-{resource}"),)

    report = run(
        model,
        session("Alpha", "Beta", cycles=2, cumulative=2),
        store,
        reader,
    )

    assert calls == [
        ("Alpha", ("selected",), 1),
        ("Beta", ("selected",), 1),
    ]
    assert store.resource_lists == 2
    assert store.appends == 2
    assert report.stop_reason is ShadowSoakStopReason.CYCLE_LIMIT
    assert report.cycles_attempted == report.cycles_completed == 2
    assert report.erp_reads == report.evidence_batches_appended == 2
    assert report.observations_persisted == 2
    assert report.distinct_entities_studied == 2
    assert report.prediction_evaluated_outcomes == 0
    assert report.evidence_only_outcomes == 2
    assert [
        batch.sequence for batch in store.store.load_all(tenant_id=TENANT, resource="Alpha")
    ] == [1]
    assert [
        batch.sequence for batch in store.store.load_all(tenant_id=TENANT, resource="Beta")
    ] == [1]


def test_restart_retains_attempts_and_rotates_equally_covered_targets(tmp_path):
    model = understanding("Alpha", "Beta")
    store = SQLiteHistoricalEvidenceStore(tmp_path / "evidence.sqlite3")

    run(
        model,
        session("Alpha", "Beta", cycles=2, cumulative=2),
        store,
        lambda resource, *_: (observation(resource, f"initial-{resource}"),),
    )
    calls = []
    report = run(
        model,
        session("Alpha", "Beta", cycles=2, cumulative=2),
        store,
        lambda resource, *_: calls.append(resource)
        or (observation(resource, f"initial-{resource}"),),
    )

    assert calls == ["Alpha", "Beta"]
    assert report.stop_reason is ShadowSoakStopReason.CYCLE_LIMIT
    assert report.failure_category_counts == (("no_progress", 2),)


def test_retained_history_does_not_hide_unstudied_authorized_alternative(tmp_path):
    model = understanding("Alpha", "Beta")
    store = SQLiteHistoricalEvidenceStore(tmp_path / "evidence.sqlite3")
    run(
        model,
        session("Alpha", cycles=1, cumulative=1),
        store,
        lambda resource, *_: (observation(resource, "initial"),),
    )
    calls = []

    report = run(
        model,
        session("Alpha", "Beta", cycles=1, cumulative=1),
        store,
        lambda resource, *_: calls.append(resource) or (observation(resource, "new"),),
    )

    assert calls == ["Beta"]
    assert report.stop_reason is ShadowSoakStopReason.CYCLE_LIMIT
    assert report.failure_category_counts == ()


def test_restart_stops_after_five_unchanged_batches_without_sixth_read(tmp_path):
    model = understanding("Alpha")
    store = SQLiteHistoricalEvidenceStore(tmp_path / "evidence.sqlite3")
    run(
        model,
        session("Alpha", cycles=1, cumulative=1),
        store,
        lambda resource, *_: (observation(resource, "stable"),),
    )
    calls = []

    report = run(
        model,
        session("Alpha", cycles=10, reads=10, cumulative=10, failures=5),
        store,
        lambda resource, *_: calls.append(resource)
        or (observation(resource, "stable"),),
    )

    assert calls == ["Alpha"] * 5
    assert report.stop_reason is ShadowSoakStopReason.NON_PROGRESS_LIMIT
    assert report.cycles_attempted == report.cycles_completed == 5
    assert report.erp_reads == report.evidence_batches_appended == 5
    assert report.observations_persisted == 5
    assert report.failure_category_counts == (("no_progress", 5),)


def test_changed_authorized_value_resets_consecutive_non_progress(tmp_path):
    model = understanding("Alpha")
    store = SQLiteHistoricalEvidenceStore(tmp_path / "evidence.sqlite3")
    run(
        model,
        session("Alpha", cycles=1, cumulative=1),
        store,
        lambda resource, *_: (observation(resource, "before"),),
    )
    values = iter(("before", "after", "after", "after"))
    calls = []

    report = run(
        model,
        session("Alpha", cycles=10, reads=10, cumulative=10, failures=2),
        store,
        lambda resource, *_: calls.append(resource)
        or (observation(resource, next(values)),),
    )

    assert calls == ["Alpha"] * 4
    assert report.stop_reason is ShadowSoakStopReason.NON_PROGRESS_LIMIT
    assert report.cycles_attempted == report.cycles_completed == 4
    assert report.failure_category_counts == (("no_progress", 3),)


def test_persistence_failure_stops_before_another_read(tmp_path):
    model = understanding("Alpha")

    class FailingStore(CountingStore):
        def append(self, batch):
            super().append(batch)
            raise RuntimeError("synthetic append failure")

    store = FailingStore(tmp_path / "evidence.sqlite3")
    reads = []
    report = run(
        model,
        session("Alpha"),
        store,
        lambda resource, *_: reads.append(resource) or (observation(resource, 1),),
    )

    assert reads == ["Alpha"]
    assert report.stop_reason is ShadowSoakStopReason.PERSISTENCE_FAILURE
    assert report.erp_reads == 1
    assert report.cycles_completed == 0
    assert report.evidence_batches_appended == 1
    assert report.observations_persisted == 1
    assert report.failure_category_counts == (
        ("persistence_failure_after_verified_append", 1),
    )
    assert len(store.store.load_all(tenant_id=TENANT, resource="Alpha")) == 1


@pytest.mark.parametrize(
    "limits,expected",
    (
        (session("Alpha", cycles=1), ShadowSoakStopReason.CYCLE_LIMIT),
        (session("Alpha", reads=1), ShadowSoakStopReason.READ_LIMIT),
        (
            session("Alpha", cumulative=1),
            ShadowSoakStopReason.OBSERVATION_LIMIT,
        ),
    ),
)
def test_session_count_budgets_stop_deterministically(tmp_path, limits, expected):
    model = understanding("Alpha")
    store = SQLiteHistoricalEvidenceStore(tmp_path / f"{expected}.sqlite3")
    report = run(
        model,
        limits,
        store,
        lambda resource, *_: (observation(resource, 1),),
    )

    assert report.stop_reason is expected
    assert report.erp_reads == 1
    assert report.observations_persisted == 1


def test_remaining_observation_budget_narrows_final_study(tmp_path):
    bounds = []

    def reader(resource, fields, bound):
        bounds.append(bound)
        return tuple(observation(resource, index, index=index) for index in range(1, bound + 1))

    report = run(
        understanding("Alpha"),
        session("Alpha", cycles=3, per_study=2, cumulative=3),
        SQLiteHistoricalEvidenceStore(tmp_path / "evidence.sqlite3"),
        reader,
    )

    assert bounds == [2, 1]
    assert report.stop_reason is ShadowSoakStopReason.OBSERVATION_LIMIT
    assert report.observations_persisted == 3


def test_fake_clock_enforces_multi_hour_wall_budget_before_read(tmp_path):
    times = iter((0.0, 6 * 60 * 60, 6 * 60 * 60))
    reads = []
    report = run(
        understanding("Alpha"),
        session("Alpha", seconds=6 * 60 * 60),
        SQLiteHistoricalEvidenceStore(tmp_path / "evidence.sqlite3"),
        lambda *args: reads.append(args),
        monotonic=lambda: next(times),
    )

    assert reads == []
    assert report.stop_reason is ShadowSoakStopReason.DURATION_LIMIT
    assert report.elapsed_seconds == 6 * 60 * 60


def test_deadline_rechecked_after_durable_reload_before_read(tmp_path):
    times = iter((0.0, 0.0, 2.0, 2.0))
    reads = []
    report = run(
        understanding("Alpha"),
        session("Alpha", seconds=1),
        SQLiteHistoricalEvidenceStore(tmp_path / "evidence.sqlite3"),
        lambda *args: reads.append(args),
        monotonic=lambda: next(times),
    )

    assert reads == []
    assert report.erp_reads == 0
    assert report.stop_reason is ShadowSoakStopReason.DURATION_LIMIT
    assert report.elapsed_seconds == 2


def test_termination_rechecked_after_durable_reload_before_read(tmp_path):
    requested = iter((False, True))
    reads = []
    report = run(
        understanding("Alpha"),
        session("Alpha"),
        SQLiteHistoricalEvidenceStore(tmp_path / "evidence.sqlite3"),
        lambda *args: reads.append(args),
        termination_requested=lambda: next(requested),
    )

    assert reads == []
    assert report.erp_reads == 0
    assert report.stop_reason is ShadowSoakStopReason.USER_TERMINATION


@pytest.mark.parametrize("kind", ("child", "single"))
def test_unsupported_structure_stops_at_non_progress_threshold(tmp_path, kind):
    model = understanding("Alpha", **{kind: True})
    reads = []
    report = run(
        model,
        session("Alpha", failures=2),
        SQLiteHistoricalEvidenceStore(tmp_path / f"{kind}.sqlite3"),
        lambda *args: reads.append(args),
    )

    assert reads == []
    assert report.stop_reason is ShadowSoakStopReason.NON_PROGRESS_LIMIT
    assert report.cycles_attempted == 2
    assert report.unsupported_proposal_count == 2
    assert report.failure_category_counts == (("unsupported_capability", 2),)


def test_repeated_reader_contract_failures_are_bounded(tmp_path):
    reads = []

    def broken_reader(*args):
        reads.append(args)
        raise RuntimeError("response body must never enter report")

    report = run(
        understanding("Alpha"),
        session("Alpha", failures=2),
        SQLiteHistoricalEvidenceStore(tmp_path / "evidence.sqlite3"),
        broken_reader,
    )

    assert len(reads) == 2
    assert report.stop_reason is ShadowSoakStopReason.ERP_CONTRACT_FAILURE
    assert report.failure_category_counts == (("erp_contract_failure", 2),)


def test_pre_read_runner_failure_does_not_claim_an_erp_read(tmp_path):
    def fails_before_opener(request, evidence_sink, permit_read):
        raise ValueError("synthetic preflight rejection")

    report = run(
        understanding("Alpha"),
        session("Alpha", failures=1),
        SQLiteHistoricalEvidenceStore(tmp_path / "evidence.sqlite3"),
        study_runner=fails_before_opener,
    )

    assert report.erp_reads == 0
    assert report.stop_reason is ShadowSoakStopReason.ERP_CONTRACT_FAILURE


def test_study_runner_cannot_consume_more_than_one_read_per_cycle(tmp_path):
    def double_read(request, evidence_sink, permit_read):
        permit_read()
        permit_read()

    report = run(
        understanding("Alpha"),
        session("Alpha", failures=1),
        SQLiteHistoricalEvidenceStore(tmp_path / "evidence.sqlite3"),
        study_runner=double_read,
    )

    assert report.erp_reads == 1
    assert report.evidence_batches_appended == 0
    assert report.stop_reason is ShadowSoakStopReason.ERP_CONTRACT_FAILURE


def test_runner_cannot_persist_without_consuming_read_permit(tmp_path):
    model = understanding("Alpha")
    limits = session("Alpha", failures=1)

    def bypasses_permit(request, evidence_sink, permit_read):
        evidence_sink(request, (observation("Alpha", 1),))

    store = CountingStore(tmp_path / "evidence.sqlite3")
    report = run(
        model,
        limits,
        store,
        study_runner=bypasses_permit,
    )

    assert report.erp_reads == 0
    assert report.evidence_batches_appended == 0
    assert store.appends == 0
    assert report.stop_reason is ShadowSoakStopReason.ERP_CONTRACT_FAILURE


def test_runner_cannot_persist_observations_over_authorized_bound(tmp_path):
    model = understanding("Alpha")
    limits = session("Alpha", per_study=1, cumulative=1, failures=1)

    def exceeds_bound(request, evidence_sink, permit_read):
        permit_read()
        evidence_sink(
            request,
            (observation("Alpha", 1, index=1), observation("Alpha", 2, index=2)),
        )

    store = CountingStore(tmp_path / "evidence.sqlite3")
    report = run(
        model,
        limits,
        store,
        study_runner=exceeds_bound,
    )

    assert report.erp_reads == 1
    assert report.observations_persisted == 0
    assert report.evidence_batches_appended == 0
    assert store.appends == 0
    assert report.stop_reason is ShadowSoakStopReason.ERP_CONTRACT_FAILURE


def test_evidence_sink_is_one_shot_and_second_call_cannot_append(tmp_path):
    model = understanding("Alpha")
    limits = session("Alpha", failures=1)

    def invokes_twice(request, evidence_sink, permit_read):
        permit_read()
        values = (observation("Alpha", 1),)
        evidence_sink(request, values)
        evidence_sink(request, values)

    store = CountingStore(tmp_path / "evidence.sqlite3")
    report = run(
        model,
        limits,
        store,
        study_runner=invokes_twice,
    )

    assert report.erp_reads == 1
    assert report.evidence_batches_appended == 1
    assert report.observations_persisted == 1
    assert store.appends == 1
    assert len(store.store.load_all(tenant_id=TENANT, resource="Alpha")) == 1
    assert report.stop_reason is ShadowSoakStopReason.PERSISTENCE_FAILURE


def test_malformed_runner_outcome_is_safely_categorized(tmp_path):
    def malformed_outcome(request, evidence_sink, permit_read):
        permit_read()

    report = run(
        understanding("Alpha"),
        session("Alpha", failures=1),
        SQLiteHistoricalEvidenceStore(tmp_path / "evidence.sqlite3"),
        study_runner=malformed_outcome,
    )

    assert report.erp_reads == 1
    assert report.evidence_batches_appended == 0
    assert report.stop_reason is ShadowSoakStopReason.ERP_CONTRACT_FAILURE


def test_outcome_kind_cannot_cross_authorized_request(tmp_path):
    model = understanding("Alpha")
    limits = session("Alpha", failures=1)

    def wrong_kind(request, evidence_sink, permit_read):
        permit_read()
        values = (observation("Alpha", 1),)
        evidence_sink(request, values)
        return StudyOutcome(
            entity="Alpha",
            fields=("selected",),
            observations_acquired=1,
            valid_count=1,
            coverage_change=0.0,
            uncertainty_reduction=0.0,
            information_gain="none",
            hypothesis_state="INCONCLUSIVE",
            study_kind="metadata_gap",
            prediction_evaluated=False,
        )

    store = CountingStore(tmp_path / "evidence.sqlite3")
    report = run(model, limits, store, study_runner=wrong_kind)

    assert report.stop_reason is ShadowSoakStopReason.PERSISTENCE_FAILURE
    assert report.cycles_completed == 0
    assert report.evidence_batches_appended == 1
    assert report.observations_persisted == 1
    assert store.appends == 1


def test_record_limit_selector_cannot_widen_session_bound(tmp_path):
    reads = []
    report = run(
        understanding("Alpha"),
        session("Alpha", per_study=2, cumulative=2),
        SQLiteHistoricalEvidenceStore(tmp_path / "evidence.sqlite3"),
        lambda *args: reads.append(args),
        record_limit_selector=lambda _opportunity, upper_bound: upper_bound + 1,
    )

    assert reads == []
    assert report.erp_reads == 0
    assert report.stop_reason is ShadowSoakStopReason.NO_AUTHORIZED_CANDIDATE


def test_tenant_mismatch_stops_immediately_without_append(tmp_path):
    report = run(
        understanding("Alpha"),
        session("Alpha", failures=5),
        SQLiteHistoricalEvidenceStore(tmp_path / "evidence.sqlite3"),
        lambda resource, *_: (observation(resource, 1, tenant="other-tenant"),),
    )

    assert report.stop_reason is ShadowSoakStopReason.TENANT_SCOPE_MISMATCH
    assert report.erp_reads == 1
    assert report.evidence_batches_appended == 0


def test_empty_reads_are_evidence_only_but_do_not_persist(tmp_path):
    report = run(
        understanding("Alpha"),
        session("Alpha", failures=2),
        SQLiteHistoricalEvidenceStore(tmp_path / "evidence.sqlite3"),
        lambda *_: (),
    )

    assert report.stop_reason is ShadowSoakStopReason.NON_PROGRESS_LIMIT
    assert report.erp_reads == 2
    assert report.evidence_only_outcomes == 2
    assert report.prediction_evaluated_outcomes == 0
    assert report.observations_persisted == 0


def test_no_authorized_candidate_performs_no_read(tmp_path):
    model = understanding("Alpha")
    limits = ShadowSoakSessionEnvelope(
        authorization(cycles=1),
        60,
        1,
        1,
        1,
        1,
        1,
    )
    reads = []
    report = run(
        model,
        limits,
        SQLiteHistoricalEvidenceStore(tmp_path / "evidence.sqlite3"),
        lambda *args: reads.append(args),
    )

    assert reads == []
    assert report.stop_reason is ShadowSoakStopReason.NO_AUTHORIZED_CANDIDATE


def test_no_candidate_performs_no_read(tmp_path):
    reads = []
    report = run(
        understanding(),
        session(cycles=1, cumulative=1),
        SQLiteHistoricalEvidenceStore(tmp_path / "evidence.sqlite3"),
        lambda *args: reads.append(args),
    )

    assert reads == []
    assert report.stop_reason is ShadowSoakStopReason.NO_CANDIDATE


def test_integrity_preflight_failure_prevents_erp_read(tmp_path):
    class BrokenStore(CountingStore):
        def list_resources(self, *, tenant_id):
            raise RuntimeError("synthetic integrity failure")

    reads = []
    report = run(
        understanding("Alpha"),
        session("Alpha"),
        BrokenStore(tmp_path / "evidence.sqlite3"),
        lambda *args: reads.append(args),
    )

    assert reads == []
    assert report.stop_reason is ShadowSoakStopReason.PERSISTENCE_FAILURE
    assert report.failure_category_counts == (("persistence_integrity_failure", 1),)


def test_report_contains_only_safe_aggregates_and_no_authority(tmp_path):
    report = run(
        understanding("Alpha"),
        session("Alpha", cycles=1),
        SQLiteHistoricalEvidenceStore(tmp_path / "evidence.sqlite3"),
        lambda resource, *_: (observation(resource, "raw-customer-secret"),),
    )
    rendered = repr(asdict(report))

    assert "raw-customer-secret" not in rendered
    assert "private-company-value" not in rendered
    assert report.erp_writes == 0
    assert not report.recommendation_allowed
    assert not report.promotion_allowed
    assert not report.execution_allowed
    assert report.first_selected_target_type == StudyCapability.ORDINARY_RECORD.value
    assert report.final_selected_target_type == StudyCapability.ORDINARY_RECORD.value


def test_termination_request_stops_without_read(tmp_path):
    reads = []
    report = run(
        understanding("Alpha"),
        session("Alpha"),
        SQLiteHistoricalEvidenceStore(tmp_path / "evidence.sqlite3"),
        lambda *args: reads.append(args),
        termination_requested=lambda: True,
    )

    assert reads == []
    assert report.stop_reason is ShadowSoakStopReason.USER_TERMINATION


def test_preferred_six_hour_limits_require_matching_authorization():
    broad = authorization("Alpha", cycles=100, per_study=5, cumulative=500)
    limits = ShadowSoakSessionEnvelope.six_hour(broad)

    assert limits.max_wall_clock_seconds == 21_600
    assert limits.max_study_cycles == limits.max_erp_reads == 100
    assert limits.max_observations_per_study == 5
    assert limits.max_cumulative_observations == 500
    assert limits.max_consecutive_non_progress == 5
    assert limits.observation_mode == "READ_ONLY"


class FakeResponse:
    def __init__(self, request, payload):
        self._url = request.full_url
        self._body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def geturl(self):
        return self._url

    def read(self, limit):
        return self._body[:limit]


def test_runtime_composes_with_exact_identity_router_and_narrows_bound(tmp_path):
    model = MetadataUnderstanding(
        TENANT,
        (
            StructuralEntity(
                "Alpha",
                None,
                False,
                False,
                False,
                (field("Alpha", "selected", required=True),),
                (),
            ),
        ),
    )
    limits = ShadowSoakSessionEnvelope(
        authorization("Alpha", cycles=1, per_study=5, cumulative=5),
        max_wall_clock_seconds=60,
        max_study_cycles=1,
        max_erp_reads=1,
        max_observations_per_study=5,
        max_cumulative_observations=5,
        max_consecutive_non_progress=1,
    )
    requests = []

    def routed_runner(request, evidence_sink, permit_read):
        def opener(http_request, *, timeout):
            permit_read()
            requests.append((http_request, timeout))
            return FakeResponse(
                http_request,
                {"data": [{"name": "record-1", "selected": "synthetic"}]},
            )

        return run_erpnext_governed_study(
            request,
            envelope=limits.authorization,
            understanding=model,
            base_url="https://synthetic.invalid",
            api_key="synthetic-key",
            api_secret="synthetic-secret",
            record_identity="record-1",
            opener=opener,
            evidence_sink=evidence_sink,
        )

    report = run_autonomous_shadow_soak(
        OBJECTIVE,
        model,
        limits,
        store=SQLiteHistoricalEvidenceStore(tmp_path / "evidence.sqlite3"),
        study_runner=routed_runner,
        record_limit_selector=lambda opportunity, upper_bound: 1,
        clock=lambda: START,
        monotonic=lambda: 0.0,
    )

    assert report.stop_reason is ShadowSoakStopReason.CYCLE_LIMIT
    assert report.erp_reads == report.observations_persisted == 1
    assert len(requests) == 1
    query = parse_qs(urlparse(requests[0][0].full_url).query)
    assert query["limit_page_length"] == ["1"]


def test_activity_events_follow_successful_semantic_boundaries(tmp_path):
    events = []
    run_id = UUID("00000000-0000-4000-8000-000000000055")
    report = run(
        understanding("Alpha", "Beta"),
        session("Alpha", "Beta", cycles=2, cumulative=2),
        SQLiteHistoricalEvidenceStore(tmp_path / "evidence.sqlite3"),
        lambda resource, *_: (observation(resource, f"private-{resource}"),),
        activity_sink=events.append,
        activity_run_id=run_id,
        activity_clock=lambda: START,
    )

    assert [event.event_type for event in events] == [
        ActivityEventType.RUN_STARTED,
        ActivityEventType.OBJECTIVE_LOADED,
        ActivityEventType.PROPOSAL_SELECTED,
        ActivityEventType.AUTHORIZATION_CHECKED,
        ActivityEventType.READ_STARTED,
        ActivityEventType.READ_COMPLETED,
        ActivityEventType.VALIDATION_COMPLETED,
        ActivityEventType.EVIDENCE_SINK_STARTED,
        ActivityEventType.EVIDENCE_PERSISTED,
        ActivityEventType.REASSESSMENT_STARTED,
        ActivityEventType.NEXT_PROPOSAL_SELECTED,
        ActivityEventType.AUTHORIZATION_CHECKED,
        ActivityEventType.READ_STARTED,
        ActivityEventType.READ_COMPLETED,
        ActivityEventType.VALIDATION_COMPLETED,
        ActivityEventType.EVIDENCE_SINK_STARTED,
        ActivityEventType.EVIDENCE_PERSISTED,
        ActivityEventType.RUN_STOPPED,
    ]
    assert report.cycles_completed == 2
    assert all(event.run_id == run_id and event.erp_writes == 0 for event in events)
    read_events = [
        event
        for event in events
        if event.event_type
        in {
            ActivityEventType.READ_STARTED,
            ActivityEventType.READ_COMPLETED,
            ActivityEventType.VALIDATION_COMPLETED,
            ActivityEventType.EVIDENCE_SINK_STARTED,
            ActivityEventType.EVIDENCE_PERSISTED,
        }
    ]
    assert [event.erp_reads for event in read_events] == [1] * 5 + [2] * 5
    persisted = [
        event
        for event in events
        if event.event_type is ActivityEventType.EVIDENCE_PERSISTED
    ]
    assert [event.evidence_batches_appended for event in persisted] == [1, 2]
    rendered = repr(events)
    assert TENANT not in rendered
    assert "private-company-value" not in rendered
    assert "private-Alpha" not in rendered
    assert "private-Beta" not in rendered


def test_absent_activity_sink_preserves_exact_report(tmp_path):
    first = run(
        understanding("Alpha"),
        session("Alpha", cycles=1),
        SQLiteHistoricalEvidenceStore(tmp_path / "first.sqlite3"),
        lambda resource, *_: (observation(resource, 1),),
    )
    second = run(
        understanding("Alpha"),
        session("Alpha", cycles=1),
        SQLiteHistoricalEvidenceStore(tmp_path / "second.sqlite3"),
        lambda resource, *_: (observation(resource, 1),),
        activity_sink=None,
    )
    assert first == second


def test_denied_authorization_emits_denial_and_no_read_started(tmp_path):
    events = []
    report = run(
        understanding("Alpha"),
        session(cycles=1, cumulative=1),
        SQLiteHistoricalEvidenceStore(tmp_path / "evidence.sqlite3"),
        lambda *_: pytest.fail("reader must not run"),
        activity_sink=events.append,
        activity_run_id=UUID("00000000-0000-4000-8000-000000000055"),
        activity_clock=lambda: START,
    )
    kinds = [event.event_type for event in events]
    assert report.stop_reason is ShadowSoakStopReason.NO_AUTHORIZED_CANDIDATE
    assert ActivityEventType.AUTHORIZATION_DENIED in kinds
    assert ActivityEventType.READ_STARTED not in kinds


def test_failed_read_emits_only_sanitized_failure(tmp_path):
    events = []

    def reader(*args):
        raise RuntimeError("raw-response private-company-value")

    report = run(
        understanding("Alpha"),
        session("Alpha", failures=1),
        SQLiteHistoricalEvidenceStore(tmp_path / "evidence.sqlite3"),
        reader,
        activity_sink=events.append,
        activity_run_id=UUID("00000000-0000-4000-8000-000000000055"),
        activity_clock=lambda: START,
    )
    kinds = [event.event_type for event in events]
    assert report.stop_reason is ShadowSoakStopReason.ERP_CONTRACT_FAILURE
    assert ActivityEventType.READ_STARTED in kinds
    assert ActivityEventType.SAFE_FAILURE in kinds
    assert ActivityEventType.EVIDENCE_PERSISTED not in kinds
    assert "raw-response" not in repr(events)
    assert "private-company-value" not in repr(events)


def test_validation_failure_never_emits_evidence_persisted(tmp_path):
    events = []

    def invalid_scope(request, evidence_sink, permit_read):
        permit_read()
        evidence_sink(request, (observation("Alpha", 1, tenant="other-tenant"),))

    report = run(
        understanding("Alpha"),
        session("Alpha", failures=1),
        SQLiteHistoricalEvidenceStore(tmp_path / "evidence.sqlite3"),
        study_runner=invalid_scope,
        activity_sink=events.append,
        activity_run_id=UUID("00000000-0000-4000-8000-000000000055"),
        activity_clock=lambda: START,
    )
    kinds = [event.event_type for event in events]
    assert report.stop_reason is ShadowSoakStopReason.TENANT_SCOPE_MISMATCH
    assert ActivityEventType.READ_COMPLETED in kinds
    assert ActivityEventType.VALIDATION_COMPLETED not in kinds
    assert ActivityEventType.EVIDENCE_PERSISTED not in kinds


def test_activity_sink_failure_cannot_change_soak_behavior(tmp_path):
    reads = []

    def broken_sink(event):
        raise RuntimeError("watcher unavailable")

    report = run(
        understanding("Alpha"),
        session("Alpha", cycles=1),
        SQLiteHistoricalEvidenceStore(tmp_path / "evidence.sqlite3"),
        lambda resource, *_: reads.append(resource) or (observation(resource, 1),),
        activity_sink=broken_sink,
        activity_run_id=UUID("00000000-0000-4000-8000-000000000055"),
        activity_clock=lambda: START,
    )
    assert reads == ["Alpha"]
    assert report.stop_reason is ShadowSoakStopReason.CYCLE_LIMIT
    assert report.cycles_completed == report.erp_reads == report.observations_persisted == 1
    assert report.erp_writes == 0 and report.execution_allowed is False


def test_activity_event_is_immutable_and_cannot_grant_authority():
    event = ActivityEvent(
        event_type=ActivityEventType.RUN_STARTED,
        occurred_at=START,
        run_id=UUID("00000000-0000-4000-8000-000000000055"),
        erp_reads=0,
        evidence_batches_appended=0,
    )
    assert event.recommendation_allowed is False
    assert event.promotion_allowed is False
    assert event.execution_allowed is False
    assert event.erp_writes == 0
    with pytest.raises(AttributeError):
        event.execution_allowed = True

    forbidden = {
        "tenant_id",
        "company",
        "payload",
        "raw_value",
        "credential",
        "origin_url",
        "provenance_id",
        "document_id",
    }
    assert forbidden.isdisjoint(ActivityEvent.__dataclass_fields__)


def test_terminal_watcher_renders_safe_structural_timeline_only():
    stream = StringIO()
    watcher = TerminalActivityWatcher(stream)
    event = ActivityEvent(
        event_type=ActivityEventType.EVIDENCE_PERSISTED,
        occurred_at=START,
        run_id=UUID("00000000-0000-4000-8000-000000000055"),
        cycle=1,
        study_kind="record_evidence",
        entity="Alpha",
        fields=("selected",),
        requested_records=1,
        observations_acquired=1,
        valid_count=1,
        erp_reads=1,
        evidence_batches_appended=1,
        persistence_verified=True,
    )
    watcher(event)
    rendered = stream.getvalue()
    assert rendered == render_activity_event(event) + "\n"
    assert "evidence_persisted" in rendered
    assert "Alpha.selected" in rendered
    assert "reads=1" in rendered and "writes=0" in rendered
    assert TENANT not in rendered
    assert "private-company-value" not in rendered


@pytest.mark.parametrize("unsafe", ("bad\nlabel", "bad\x1b[31m", ""))
def test_activity_event_rejects_unsafe_structural_labels(unsafe):
    with pytest.raises(ValueError):
        ActivityEvent(
            event_type=ActivityEventType.PROPOSAL_SELECTED,
            occurred_at=START,
            run_id=UUID("00000000-0000-4000-8000-000000000055"),
            entity=unsafe,
            erp_reads=0,
            evidence_batches_appended=0,
        )


def test_synthetic_watcher_demo_exposes_timeline_without_payload(tmp_path):
    stream = StringIO()
    report = run_watcher_demo(stream, evidence_path=tmp_path / "evidence.sqlite3")
    rendered = stream.getvalue()
    assert report.cycles_completed == 1
    assert report.erp_reads == 1 and report.erp_writes == 0
    assert report.execution_allowed is False
    assert "run_started" in rendered
    assert "evidence_persisted" in rendered
    assert "run_stopped" in rendered
    assert "SyntheticEntity.selected_field" in rendered
    assert "synthetic-demo-tenant" not in rendered
    assert "synthetic-company-value" not in rendered
    assert "synthetic-record-value" not in rendered
