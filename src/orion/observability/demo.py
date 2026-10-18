"""Offline synthetic shadow-soak demonstration for the terminal watcher."""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TextIO
from uuid import UUID

from ..contracts import Evidence, EvidenceKind, Observation
from ..learning.autonomous_loop import AuthorizationEnvelope, LearningObjective
from ..learning.shadow_soak import (
    ShadowSoakReport,
    ShadowSoakSessionEnvelope,
    run_autonomous_shadow_soak,
)
from ..learning.study_capability import (
    StudyCapability,
    run_routed_governed_record_evidence,
)
from ..stores.sqlite_historical_evidence import SQLiteHistoricalEvidenceStore
from ..understanding.metadata import (
    MetadataUnderstanding,
    StructuralEntity,
    StructuralField,
)
from .terminal import TerminalActivityWatcher

_TENANT = "synthetic-demo-tenant"
_ENTITY = "SyntheticEntity"
_FIELD = "selected_field"
_START = datetime(2026, 1, 1, tzinfo=UTC)


def _understanding() -> MetadataUnderstanding:
    selected = StructuralField(
        _ENTITY,
        _FIELD,
        "Data",
        None,
        None,
        True,
        False,
        False,
        False,
    )
    company = StructuralField(
        _ENTITY,
        "company",
        "Data",
        None,
        None,
        True,
        False,
        False,
        False,
    )
    return MetadataUnderstanding(
        _TENANT,
        (
            StructuralEntity(
                _ENTITY,
                None,
                False,
                False,
                False,
                (selected, company),
                (),
            ),
        ),
    )


def _observation() -> Observation:
    return Observation(
        Evidence(
            kind=EvidenceKind.API,
            source="synthetic-watcher-demo",
            tenant_id=_TENANT,
            observed_at=_START,
            payload={
                "resource": _ENTITY,
                "record": {
                    "name": "synthetic-record",
                    "company": "synthetic-company-value",
                    _FIELD: "synthetic-record-value",
                },
            },
        )
    )


def run_demo(
    stream: TextIO,
    *,
    evidence_path: Path,
) -> ShadowSoakReport:
    """Run one offline synthetic cycle and stream only sanitized activity."""

    model = _understanding()
    objective = LearningObjective("watcher-demo", "exercise sanitized activity")
    authorization = AuthorizationEnvelope(
        _TENANT,
        objective.objective_id,
        allowed_record_entities=frozenset({_ENTITY}),
        allowed_record_fields=((_ENTITY, (_FIELD,)),),
        max_cycles=1,
        max_records_per_proposal=1,
        max_cumulative_records=1,
    )
    session = ShadowSoakSessionEnvelope(
        authorization=authorization,
        max_wall_clock_seconds=60,
        max_study_cycles=1,
        max_erp_reads=1,
        max_observations_per_study=1,
        max_cumulative_observations=1,
        max_consecutive_non_progress=1,
    )

    def reader(resource, fields, requested_records):
        if (resource, fields, requested_records) != (_ENTITY, (_FIELD,), 1):
            raise ValueError("synthetic reader scope changed")
        return (_observation(),)

    def study_runner(request, evidence_sink, permit_read):
        permit_read()
        return run_routed_governed_record_evidence(
            request,
            envelope=authorization,
            understanding=model,
            readers={StudyCapability.ORDINARY_RECORD: reader},
            evidence_sink=evidence_sink,
        )

    return run_autonomous_shadow_soak(
        objective,
        model,
        session,
        store=SQLiteHistoricalEvidenceStore(evidence_path),
        study_runner=study_runner,
        clock=lambda: _START,
        monotonic=lambda: 0.0,
        activity_sink=TerminalActivityWatcher(stream),
        activity_run_id=UUID("00000000-0000-4000-8000-000000000055"),
        activity_clock=lambda: _START,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-db", type=Path)
    args = parser.parse_args(argv)
    if args.evidence_db is not None:
        report = run_demo(sys.stdout, evidence_path=args.evidence_db)
    else:
        with TemporaryDirectory(prefix="orion-watcher-") as directory:
            report = run_demo(
                sys.stdout,
                evidence_path=Path(directory) / "evidence.sqlite3",
            )
    if report.erp_writes or report.execution_allowed:
        raise RuntimeError("watcher demo crossed its observation-only boundary")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
