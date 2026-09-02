"""Portable offline composition for proposal-only autonomous study selection."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..history.evidence import HistoricalEvidenceBatch, HistoricalEvidenceError
from ..stores.sqlite_checkpoint import SQLiteStudyCheckpointStore
from ..stores.sqlite_historical_evidence import SQLiteHistoricalEvidenceStore
from ..understanding.metadata import MetadataUnderstanding
from .autonomous_loop import (
    EvidenceCoverage,
    LearningObjective,
    StudyOpportunity,
    discover_opportunities,
    is_missing_evidence,
)


def project_historical_coverage(
    understanding: MetadataUnderstanding,
    batches: Sequence[HistoricalEvidenceBatch],
) -> tuple[EvidenceCoverage, ...]:
    """Project captured keys into aggregate coverage without exposing values.

    A captured value is missing only when it is ``None`` or a blank string.
    Absent keys are unobserved; numeric zero and ``False`` are valid values.
    """

    if not isinstance(understanding, MetadataUnderstanding):
        raise TypeError("understanding must be MetadataUnderstanding")

    scopes: dict[tuple[str, str], list[Any]] = {}
    fields_by_entity: dict[str, frozenset[str]] = {}
    for entity in understanding.entities:
        if entity.doctype in fields_by_entity:
            raise HistoricalEvidenceError("duplicate structural entity")
        names = [field.fieldname for field in entity.fields]
        if len(names) != len(set(names)):
            raise HistoricalEvidenceError("duplicate structural field")
        fields_by_entity[entity.doctype] = frozenset(names)
        for name in names:
            scopes[(entity.doctype, name)] = [0, 0, 0, set()]

    for batch in batches:
        if not isinstance(batch, HistoricalEvidenceBatch):
            raise HistoricalEvidenceError(
                "batches must contain HistoricalEvidenceBatch values"
            )
        if batch.tenant_id != understanding.tenant_id:
            raise HistoricalEvidenceError("historical evidence crosses tenant boundary")
        fields = fields_by_entity.get(batch.resource)
        if fields is None:
            raise HistoricalEvidenceError(
                "historical resource is absent from structural understanding"
            )
        for observation in batch.observations:
            payload = observation.evidence.payload
            if (
                not isinstance(payload, Mapping)
                or set(payload) != {"resource", "record"}
                or payload["resource"] != batch.resource
                or not isinstance(payload["record"], Mapping)
            ):
                raise HistoricalEvidenceError("malformed historical record payload")
            record = payload["record"]
            for field in fields.intersection(record):
                state = scopes[(batch.resource, field)]
                value = record[field]
                state[0] += 1
                if is_missing_evidence(value):
                    state[2] += 1
                else:
                    state[1] += 1
                    state[3].add(_canonical_value(value))

    return tuple(
        EvidenceCoverage(
            entity=entity,
            field=field,
            observations_seen=state[0],
            valid_observations=state[1],
            distinct_value_count=len(state[3]),
            missing_count=state[2],
        )
        for (entity, field), state in sorted(scopes.items())
    )


def select_study_proposal(
    objective: LearningObjective,
    understanding: MetadataUnderstanding,
    coverage: tuple[EvidenceCoverage, ...],
) -> StudyOpportunity | None:
    """Return the canonical planner's deterministic highest-ranked candidate."""

    scope = [(item.entity, item.field) for item in coverage]
    if len(scope) != len(set(scope)):
        raise ValueError("coverage contains duplicate scope")
    opportunities = discover_opportunities(objective, understanding, coverage)
    return opportunities[0] if opportunities else None


@dataclass(frozen=True, slots=True)
class OfflineProposalReport:
    checkpoint_loaded: bool
    structural_entity_count: int
    historical_resource_count: int
    historical_batch_count: int
    historical_observation_count: int
    coverage_scope_count: int
    candidate_selected: bool
    selected_study_kind: str | None
    selected_entity: str | None
    selected_fields: tuple[str, ...]
    selected_score: float | None
    selected_score_components: tuple[tuple[str, float], ...]
    selected_rationale: str | None
    proposal_only: bool = True
    authorization_granted: bool = False
    runner_called: bool = False
    erp_calls: int = 0
    erp_writes: int = 0
    recommendation_allowed: bool = False
    promotion_allowed: bool = False
    execution_allowed: bool = False


def run_offline_proposal(
    *,
    checkpoint_path: Path,
    history_path: Path,
    tenant_id: str,
    objective: LearningObjective,
) -> OfflineProposalReport:
    """Load durable offline state and produce an unapproved study proposal."""

    checkpoint_path = _existing_file(checkpoint_path, "checkpoint")
    history_path = _existing_file(history_path, "history")
    checkpoint = SQLiteStudyCheckpointStore(
        checkpoint_path,
        read_only=True,
    ).load_latest(
        tenant_id=tenant_id
    )
    if checkpoint is None:
        raise ValueError("latest checkpoint is required")
    if checkpoint.tenant_id != tenant_id:
        raise ValueError("checkpoint crosses tenant boundary")

    history_store = SQLiteHistoricalEvidenceStore(
        history_path,
        read_only=True,
    )
    resources = history_store.list_resources(tenant_id=tenant_id)
    batches = tuple(
        batch
        for resource in resources
        for batch in history_store.load_all(
            tenant_id=tenant_id,
            resource=resource,
        )
    )
    coverage = project_historical_coverage(checkpoint.understanding, batches)
    selected = select_study_proposal(
        objective,
        checkpoint.understanding,
        coverage,
    )
    return OfflineProposalReport(
        checkpoint_loaded=True,
        structural_entity_count=len(checkpoint.understanding.entities),
        historical_resource_count=len(resources),
        historical_batch_count=len(batches),
        historical_observation_count=sum(
            len(batch.observations) for batch in batches
        ),
        coverage_scope_count=len(coverage),
        candidate_selected=selected is not None,
        selected_study_kind=(selected.study_kind if selected else None),
        selected_entity=(selected.entity if selected else None),
        selected_fields=(selected.fields if selected else ()),
        selected_score=(selected.score if selected else None),
        selected_score_components=(
            selected.score_components if selected else ()
        ),
        selected_rationale=(selected.rationale if selected else None),
    )


def _canonical_value(value: object) -> str:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise HistoricalEvidenceError(
            "historical field value must be JSON-compatible"
        ) from exc


def _existing_file(path: Path, label: str) -> Path:
    if not isinstance(path, Path):
        raise TypeError(f"{label}_path must be pathlib.Path")
    if not path.is_file():
        raise ValueError(f"{label} database file is required")
    return path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Produce one offline study proposal")
    parser.add_argument("--checkpoint-db", type=Path, required=True)
    parser.add_argument("--history-db", type=Path, required=True)
    parser.add_argument("--tenant-id", required=True)
    parser.add_argument("--objective-id", required=True)
    parser.add_argument("--objective-description", required=True)
    args = parser.parse_args(argv)
    report = run_offline_proposal(
        checkpoint_path=args.checkpoint_db,
        history_path=args.history_db,
        tenant_id=args.tenant_id,
        objective=LearningObjective(
            args.objective_id,
            args.objective_description,
        ),
    )
    print(json.dumps(asdict(report), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
