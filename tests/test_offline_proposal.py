from __future__ import annotations

import inspect
import json
import socket
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from orion.contracts import Evidence, EvidenceKind, Observation
from orion.discovery.checkpoint import StudyCheckpoint
from orion.history.evidence import HistoricalEvidenceBatch, HistoricalEvidenceError
from orion.learning.autonomous_loop import (
    EvidenceCoverage,
    LearningObjective,
    discover_opportunities,
)
from orion.learning.offline_proposal import (
    main,
    project_historical_coverage,
    run_offline_proposal,
    select_study_proposal,
)
from orion.stores.sqlite_checkpoint import SQLiteStudyCheckpointStore
from orion.stores.sqlite_historical_evidence import SQLiteHistoricalEvidenceStore
from orion.understanding.metadata import (
    MetadataUnderstanding,
    StructuralEntity,
    StructuralField,
)


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


def understanding(
    *, tenant_id="tenant-a", entity="EntityA", fields=("alpha", "beta")
):
    return MetadataUnderstanding(
        tenant_id,
        (
            StructuralEntity(
                entity,
                None,
                False,
                False,
                False,
                tuple(field(entity, name) for name in fields),
                (UUID(int=1),),
            ),
        ),
    )


def batch(
    records,
    *,
    tenant_id="tenant-a",
    resource="EntityA",
    sequence=1,
):
    observations = tuple(
        Observation(
            evidence=Evidence(
                kind=EvidenceKind.API,
                source="synthetic-history",
                tenant_id=tenant_id,
                observed_at=datetime(2026, 9, 2, 10, tzinfo=UTC),
                payload={"resource": resource, "record": record},
            )
        )
        for record in records
    )
    return HistoricalEvidenceBatch(
        tenant_id,
        resource,
        sequence,
        datetime(2026, 9, 2, 11, sequence, tzinfo=UTC),
        observations,
    )


def objective():
    return LearningObjective("objective-1", "increase neutral evidence coverage")


def coverage_by_field(items):
    return {item.field: item for item in items}


def test_projection_preserves_tenant_isolation():
    with pytest.raises(HistoricalEvidenceError, match="tenant boundary"):
        project_historical_coverage(
            understanding(),
            (batch(({"name": "one", "alpha": 1},), tenant_id="tenant-b"),),
        )


def test_absent_field_is_unobserved_not_missing():
    coverage = coverage_by_field(
        project_historical_coverage(
            understanding(),
            (batch(({"name": "one", "alpha": "captured"},)),),
        )
    )

    assert coverage["beta"].observations_seen == 0
    assert coverage["beta"].missing_count == 0


def test_none_and_blank_are_missing_but_zero_and_false_are_valid():
    coverage = coverage_by_field(
        project_historical_coverage(
            understanding(fields=("alpha",)),
            (
                batch(
                    (
                        {"name": "one", "alpha": None},
                        {"name": "two", "alpha": "  "},
                        {"name": "three", "alpha": 0},
                        {"name": "four", "alpha": False},
                    )
                ),
            ),
        )
    )["alpha"]

    assert coverage.observations_seen == 4
    assert coverage.valid_observations == 2
    assert coverage.missing_count == 2
    assert coverage.distinct_value_count == 2


def test_distinct_json_values_are_deterministic_without_exposure():
    first = project_historical_coverage(
        understanding(fields=("alpha",)),
        (
            batch(
                (
                    {"name": "one", "alpha": {"x": 1, "y": [2]}},
                    {"name": "two", "alpha": {"y": [2], "x": 1}},
                    {"name": "three", "alpha": [1, 2]},
                )
            ),
        ),
    )
    second = project_historical_coverage(
        understanding(fields=("alpha",)),
        (
            batch(
                (
                    {"name": "three", "alpha": [1, 2]},
                    {"name": "two", "alpha": {"y": [2], "x": 1}},
                    {"name": "one", "alpha": {"x": 1, "y": [2]}},
                )
            ),
        ),
    )

    assert first == second
    assert first[0].distinct_value_count == 2
    assert "x" not in repr(first)


def test_unknown_resource_and_malformed_payload_fail_closed():
    with pytest.raises(HistoricalEvidenceError, match="absent"):
        project_historical_coverage(
            understanding(),
            (batch(({"name": "one"},), resource="UnknownEntity"),),
        )

    malformed = batch(({"name": "one", "alpha": 1},))
    malformed.observations[0].evidence.payload.clear()
    with pytest.raises(HistoricalEvidenceError, match="malformed"):
        project_historical_coverage(understanding(), (malformed,))


def test_projection_order_is_deterministic_for_unrelated_schemas():
    first = project_historical_coverage(
        understanding(entity="EntityZ", fields=("zeta", "alpha")),
        (batch(({"name": "one", "zeta": 1},), resource="EntityZ"),),
    )
    second = project_historical_coverage(
        understanding(entity="AnotherEntity", fields=("right", "left")),
        (
            batch(
                ({"name": "two", "left": True},),
                resource="AnotherEntity",
            ),
        ),
    )

    assert [(item.entity, item.field) for item in first] == [
        ("EntityZ", "alpha"),
        ("EntityZ", "zeta"),
    ]
    assert [(item.entity, item.field) for item in second] == [
        ("AnotherEntity", "left"),
        ("AnotherEntity", "right"),
    ]


def test_proposal_reuses_canonical_ranking_and_is_deterministic():
    model = understanding()
    coverage = project_historical_coverage(model, ())

    selected = select_study_proposal(objective(), model, coverage)

    assert selected == discover_opportunities(objective(), model, coverage)[0]
    assert selected == select_study_proposal(objective(), model, coverage)


def test_aggregate_coverage_can_change_selected_proposal():
    model = understanding()
    baseline = select_study_proposal(
        objective(), model, project_historical_coverage(model, ())
    )
    observed = select_study_proposal(
        objective(),
        model,
        project_historical_coverage(
            model,
            (batch(({"name": "one", "alpha": 1},)),),
        ),
    )

    assert baseline is not None and observed is not None
    assert baseline.fields == ("alpha",)
    assert observed.fields == ("beta",)


def test_duplicate_coverage_scope_fails_closed():
    duplicate = EvidenceCoverage("EntityA", "alpha")
    with pytest.raises(ValueError, match="duplicate scope"):
        select_study_proposal(
            objective(),
            understanding(),
            (duplicate, duplicate),
        )


def write_offline_state(tmp_path):
    checkpoint_path = tmp_path / "checkpoint.sqlite3"
    history_path = tmp_path / "history.sqlite3"
    model = understanding(tenant_id="tenant-private")
    SQLiteStudyCheckpointStore(checkpoint_path).append(
        StudyCheckpoint(
            tenant_id="tenant-private",
            sequence=1,
            created_at=datetime(2026, 9, 2, 12, tzinfo=UTC),
            understanding=model,
            sampled_records=frozenset(),
            metadata_targets_studied=(),
            record_targets_sampled=(),
        )
    )
    SQLiteHistoricalEvidenceStore(history_path).append(
        batch(
            ({"name": "document-private", "alpha": "value-private"},),
            tenant_id="tenant-private",
        )
    )
    return checkpoint_path, history_path


def test_offline_composition_is_proposal_only_and_safe(tmp_path, monkeypatch):
    checkpoint_path, history_path = write_offline_state(tmp_path)
    checkpoint_bytes = checkpoint_path.read_bytes()
    history_bytes = history_path.read_bytes()

    def reject_network(*args, **kwargs):
        raise AssertionError("network call attempted")

    monkeypatch.setattr(socket, "create_connection", reject_network)
    report = run_offline_proposal(
        checkpoint_path=checkpoint_path,
        history_path=history_path,
        tenant_id="tenant-private",
        objective=objective(),
    )
    rendered = json.dumps(report.__dict__ if hasattr(report, "__dict__") else str(report))

    assert report.proposal_only is True
    assert report.authorization_granted is False
    assert report.runner_called is False
    assert report.erp_calls == report.erp_writes == 0
    assert report.recommendation_allowed is False
    assert report.promotion_allowed is False
    assert report.execution_allowed is False
    assert checkpoint_path.read_bytes() == checkpoint_bytes
    assert history_path.read_bytes() == history_bytes
    for forbidden in (
        "tenant-private",
        "document-private",
        "value-private",
        str(checkpoint_path),
        str(history_path),
    ):
        assert forbidden not in rendered


def test_cli_uses_external_pathlib_locations_and_safe_output(tmp_path, capsys):
    checkpoint_path, history_path = write_offline_state(tmp_path)

    assert main(
        (
            "--checkpoint-db",
            str(checkpoint_path),
            "--history-db",
            str(history_path),
            "--tenant-id",
            "tenant-private",
            "--objective-id",
            "objective-1",
            "--objective-description",
            "neutral coverage",
        )
    ) == 0
    output = capsys.readouterr().out

    assert '"proposal_only": true' in output
    assert '"authorization_granted": false' in output
    for forbidden in (
        "tenant-private",
        "document-private",
        "value-private",
        str(checkpoint_path),
        str(history_path),
    ):
        assert forbidden not in output


def test_missing_checkpoint_fails_before_proposal(tmp_path):
    checkpoint_path = tmp_path / "checkpoint.sqlite3"
    history_path = tmp_path / "history.sqlite3"
    SQLiteStudyCheckpointStore(checkpoint_path)
    SQLiteHistoricalEvidenceStore(history_path)

    with pytest.raises(ValueError, match="latest checkpoint"):
        run_offline_proposal(
            checkpoint_path=checkpoint_path,
            history_path=history_path,
            tenant_id="tenant-a",
            objective=objective(),
        )


def test_public_api_has_no_human_selected_field_sequence():
    parameters = inspect.signature(run_offline_proposal).parameters
    source = inspect.getsource(run_offline_proposal)

    assert "field" not in parameters
    assert "AuthorizationEnvelope" not in source
    assert "authorize_intent" not in source
    assert "runner" not in parameters


def test_production_module_has_no_forbidden_runtime_capabilities_or_literals():
    source = Path(inspect.getfile(run_offline_proposal)).read_text(encoding="utf-8")
    forbidden = (
        "ERPNext",
        "Purchase Invoice",
        "supplier",
        "due date",
        "account",
        "tax",
        "warehouse",
        "urllib",
        "requests",
        "http://",
        "https://",
        "AuthorizationEnvelope",
        "authorize_intent",
        "run_autonomous_loop",
    )

    assert not any(item in source for item in forbidden)
    assert "C:\\" not in source
    assert "/mnt/" not in source
    assert "Path.cwd" not in source
