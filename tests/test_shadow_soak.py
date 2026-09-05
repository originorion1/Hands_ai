from dataclasses import asdict
from datetime import UTC, datetime, timedelta

import pytest

from orion.contracts import Evidence, EvidenceKind, Observation
from orion.learning.autonomous_loop import AuthorizationEnvelope, LearningObjective
from orion.learning.shadow_soak import (
    ShadowSoakSessionEnvelope,
    ShadowSoakStopReason,
    run_autonomous_shadow_soak,
)
from orion.learning.study_capability import StudyCapability
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


def run(model, limits, store, reader, **kwargs):
    return run_autonomous_shadow_soak(
        OBJECTIVE,
        model,
        limits,
        store=store,
        record_readers={
            StudyCapability.ORDINARY_RECORD: reader,
            StudyCapability.SUBMITTED_DOCUMENT: reader,
        },
        clock=kwargs.pop("clock", lambda: START),
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
    assert report.evidence_batches_appended == 0


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


def test_fake_clock_enforces_multi_hour_wall_budget_before_read(tmp_path):
    times = iter((START, START + timedelta(hours=6), START + timedelta(hours=6)))
    reads = []
    report = run(
        understanding("Alpha"),
        session("Alpha", seconds=6 * 60 * 60),
        SQLiteHistoricalEvidenceStore(tmp_path / "evidence.sqlite3"),
        lambda *args: reads.append(args),
        clock=lambda: next(times),
    )

    assert reads == []
    assert report.stop_reason is ShadowSoakStopReason.DURATION_LIMIT
    assert report.elapsed_seconds == 6 * 60 * 60


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
