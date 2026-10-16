"""Offline-first, one-use continuation after the consumed selector comparison."""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ..learning.offline_proposal import canonical_historical_value
from . import erpnext_learning_comparison as comparison
from .checkpoint import checkpoint_from_json
from .erpnext_live_session import _live_session_termination_signals
from .erpnext_metadata_preflight import _existing_private_retry_ledger
from .erpnext_metadata_refresh import _read_private_bytes


@dataclass(frozen=True)
class PostComparisonInputs(comparison.ComparisonInputs):
    prior_manifest_path: Path
    prior_manifest_sha256: str
    prior_comparison_report_path: Path
    prior_comparison_report_sha256: str


def _historical_rows(connection):
    rows = comparison._historical_rows(connection)
    rows["corrected_selector_comparison"] = connection.execute(
        "SELECT * FROM corrected_selector_comparison ORDER BY singleton"
    ).fetchall()
    return rows


def _bound_json(path, digest, directory):
    if not path.is_absolute() or path.parent != directory:
        raise comparison.LearningComparisonError("prior comparison artifact location differs")
    if comparison._digest(_read_private_bytes(path, "prior comparison artifact")) != digest:
        raise comparison.LearningComparisonError("prior comparison artifact digest differs")
    return comparison._private_json(path)


def _baseline(inputs):
    live = inputs.live()
    old = _bound_json(inputs.prior_manifest_path, inputs.prior_manifest_sha256, live.report_directory)
    report = _bound_json(
        inputs.prior_comparison_report_path, inputs.prior_comparison_report_sha256,
        live.report_directory,
    )
    if comparison._digest(inputs.authorization_reference.encode()) == old["authorization_digest"]:
        raise comparison.LearningComparisonError("post-comparison authorization must be fresh")
    ledger = _existing_private_retry_ledger(inputs.trial().preflight)
    with ledger._connect() as connection:
        comparison._lineage(inputs, connection)
        claim = connection.execute(
            "SELECT * FROM corrected_selector_comparison ORDER BY singleton"
        ).fetchall()
        if claim != [(1, "complete", old["authorization_digest"], old["source_commit"],
                      inputs.prior_manifest_sha256, 56, 100, 7, 20, "cycle_limit")]:
            raise comparison.LearningComparisonError("completed comparison claim differs")
        history_digest = comparison._json_digest(_historical_rows(connection))
    expected = {
        "status": "complete", "stop_reason": "cycle_limit", "source_commit": old["source_commit"],
        "baseline_manifest_sha256": inputs.prior_manifest_sha256,
        "prior_metadata_gets": 56, "prior_study_gets": 100, "prior_combined_gets": 156,
        "new_metadata_gets": 7, "new_study_gets": 20, "new_combined_gets": 27,
        "metadata_cumulative_gets": 63, "study_cumulative_gets": 120,
        "combined_cumulative_gets": 183, "cycles_attempted": 20, "cycles_completed": 20,
        "execution_allowed": False, "recommendation_allowed": False,
        "promotion_allowed": False, "erp_writes": 0, "descriptive_only": True,
        "baseline": old["baseline"],
    }
    if any(report.get(key) != value or type(report.get(key)) is not type(value)
           for key, value in expected.items()):
        raise comparison.LearningComparisonError("prior comparison report differs")
    rows, checkpoints, batches, understanding = comparison._state(inputs)
    old_evidence = set(old["evidence_prefix"])
    prefix_rows = tuple(row for row in rows if comparison._json_digest(row) in old_evidence)
    prefix_keys = {(row[1], row[2]) for row in prefix_rows}
    prefix_batches = tuple(batch for batch in batches if (batch.resource, batch.sequence) in prefix_keys)
    if ([comparison._json_digest(row) for row in prefix_rows] != old["evidence_prefix"]
            or [comparison._json_digest(row) for row in checkpoints[:2]] != old["checkpoint_prefix"]):
        raise comparison.LearningComparisonError("original evidence prefix changed")
    original = comparison._baseline(inputs, retained_state=(
        prefix_rows, checkpoints[:2], prefix_batches,
        checkpoint_from_json(checkpoints[1][3]).understanding,
    ))
    # Only append-only store file hashes and the new source/authorization differ.
    original["source_commit"] = old["source_commit"]
    original["authorization_digest"] = old["authorization_digest"]
    for role in ("evidence", "checkpoint"):
        original["files"][role]["sha256"] = old["files"][role]["sha256"]
    if original != old:
        raise comparison.LearningComparisonError("original reviewed baseline differs")
    added = tuple(batch for batch in batches if (batch.resource, batch.sequence) not in prefix_keys)
    captured, valid = comparison._coverage(inputs, understanding, batches)
    identities = comparison._identities(added)
    indices = {scope.entity: index for index, scope in enumerate(live.reviewed_scopes, 1)}
    if not (
        len(rows) == 120 and len(checkpoints) == 3 and len(added) == 20
        and len(comparison._identities(batches)) == 364 and len(identities) == 60
        and len({batch.resource for batch in added}) == 1
        and all(len(batch.observations) == 3 and indices[batch.resource] == 3 for batch in added)
        and set(identities).issubset(set(comparison._identities(prefix_batches)))
        and len(captured) == 6 and len({entity for entity, _ in captured}) == 3
    ):
        raise comparison.LearningComparisonError("post-comparison evidence totals differ")
    aggregate = report["comparison"]
    if any(aggregate.get(key) != value for key, value in {
        "observations": 60, "entities_covered": 3, "fields_covered": 6,
        "newly_covered_fields": 0, "new_identities": 0, "repeated_identities": 60,
        "study_distribution": [{"entity_index": 3, "study_gets": 20}],
    }.items()):
        raise comparison.LearningComparisonError("prior comparison aggregate differs")
    per_study = aggregate.get("per_study_get", [])
    if len(per_study) != 20 or any(
        row.get("study_get") != index or row.get("entity_index") != 3
        or row.get("observations") != 3 or row.get("new_identities") != 0
        or row.get("repeated_identities") != 3 or row.get("newly_covered_fields") != 0
        for index, row in enumerate(per_study, 1)
    ):
        raise comparison.LearningComparisonError("prior per-study accounting differs")
    original.update(
        source_commit=inputs.source_commit,
        authorization_digest=comparison._digest(inputs.authorization_reference.encode()),
        historical_ledger_digest=history_digest,
        evidence_prefix=[comparison._json_digest(row) for row in rows],
        checkpoint_prefix=[comparison._json_digest(row) for row in checkpoints],
        baseline={
            "metadata_gets": 63, "study_gets": 120, "combined_gets": 183,
            "cycles": 120, "observations": 364, "entities_covered": 3, "fields_covered": 6,
            "valid_fields_covered": len(valid),
            "entity_batch_distribution": sorted(Counter(batch.resource for batch in batches).values(), reverse=True),
            "distinct_identities": len(set(comparison._identities(batches))),
        },
    )
    for role, path in (
        ("evidence", comparison._paths(inputs)[0]), ("checkpoint", comparison._paths(inputs)[1]),
        ("prior_comparison_manifest", inputs.prior_manifest_path),
        ("prior_comparison_report", inputs.prior_comparison_report_path),
    ):
        original["files"][role] = {
            "path": str(path), "sha256": comparison._digest(_read_private_bytes(path, "post baseline")),
        }
    return original


class _PostMetrics(comparison._ComparisonMetrics):
    """Value changes are reported separately from identity and field coverage."""

    def __init__(self, inputs, manifest):
        super().__init__(inputs, manifest)
        self.values = {}
        self._values(self.batches, remember_only=True)

    def _values(self, batches, *, remember_only=False):
        changed = 0
        for batch in batches:
            for observation in batch.observations:
                record = observation.evidence.payload["record"]
                novel_value = False
                for field, value in record.items():
                    if field in {"name", "company", "docstatus"}:
                        continue
                    key = (batch.resource, record["name"], field)
                    signature = canonical_historical_value(value)
                    novel_value |= key in self.values and self.values[key] != signature
                    self.values[key] = signature
                changed += int(novel_value and not remember_only)
        return changed

    def collect(self):
        pending = self.pending
        previous = set(self.rows)
        super().collect()
        added = [batch for batch in self.batches if (batch.resource, batch.sequence) not in previous]
        if pending is not None:
            self.studies[-1]["changed_value_observations"] = self._values(added)

    def report(self):
        result = super().report()
        result["changed_value_observations"] = sum(
            row["changed_value_observations"] for row in self.studies
        )
        return result


POLICY = comparison.ComparisonPolicy(
    table="post_comparison_continuation", prior_metadata=63, prior_study=120,
    baseline=_baseline, historical_rows=_historical_rows, report_prefix="post-comparison-report",
    metrics_factory=_PostMetrics,
)


def prepare_post_comparison_manifest(inputs, path):
    return comparison.prepare_comparison_manifest(inputs, path, policy=POLICY)


def inspect_post_comparison_readiness(inputs, path, digest):
    return comparison.inspect_learning_comparison_readiness(inputs, path, digest, policy=POLICY)


def run_post_comparison(inputs, path, digest, **kwargs):
    return comparison.run_learning_comparison(inputs, path, digest, policy=POLICY, **kwargs)


def main(argv: Sequence[str] | None = None):
    parser = argparse.ArgumentParser(description="Bounded post-comparison continuation", allow_abbrev=False)
    for flag in ("candidate", "continuation-report", "prior-manifest", "prior-comparison-report", "manifest"):
        parser.add_argument(f"--{flag}", type=Path, required=True)
    for flag in ("candidate-sha256", "continuation-report-sha256", "prior-manifest-sha256",
                 "prior-comparison-report-sha256", "source-commit", "comparison-authorization-reference"):
        parser.add_argument(f"--{flag}", required=True)
    parser.add_argument("--manifest-sha256")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--prepare-manifest", action="store_true")
    actions.add_argument("--execute-post-comparison", action="store_true")
    args = parser.parse_args(argv)
    inputs = PostComparisonInputs(
        dict(os.environ), args.candidate, args.candidate_sha256, args.continuation_report,
        args.continuation_report_sha256, args.source_commit, args.comparison_authorization_reference,
        args.prior_manifest, args.prior_manifest_sha256,
        args.prior_comparison_report, args.prior_comparison_report_sha256,
    )
    try:
        if args.prepare_manifest:
            result = {"status": "prepared", "execution_allowed": False,
                      "manifest_sha256": prepare_post_comparison_manifest(inputs, args.manifest)}
        elif args.execute_post_comparison:
            with _live_session_termination_signals() as termination:
                result = run_post_comparison(
                    inputs, args.manifest, args.manifest_sha256 or "", termination_requested=termination,
                )
        else:
            result = inspect_post_comparison_readiness(inputs, args.manifest, args.manifest_sha256 or "")
    except Exception:  # noqa: BLE001 - no private exception text in terminal
        result = {"execution_allowed": False, "status": "post_comparison_refused"}
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["status"] in {"ready", "prepared", "complete"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
