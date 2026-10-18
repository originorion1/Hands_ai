"""Post-seal transfer diagnostics; never imported by a selector."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from evaluation.pr279_study_selection.benchmark import _digest, _observation
from evaluation.pr279_study_selection.score import score


def analyze(cases_path: Path, labels_path: Path, predictions_path: Path) -> dict:
    scored = score(predictions_path, labels_path, cases_path)
    cases = {row["case_id"]: row for row in json.loads(cases_path.read_bytes())["cases"]}
    labels = json.loads(labels_path.read_bytes())["labels"]
    sealed = json.loads(predictions_path.read_bytes())
    methods = {}
    for method in sealed["method_order"]:
        rows = [row for row in sealed["predictions"] if row["method"] == method]
        total_edges = selected_edges = correct_abstentions = negative_cases = 0
        details = []
        domains = defaultdict(lambda: {"tp": 0, "fn": 0, "resolved_edges": 0})
        for row in rows:
            case = cases[row["case_id"]]
            target = row["selected_target"]
            record = case["records"][target]
            structural = _observation(case, target, metadata=True).evidence
            acquired = _observation(case, target, metadata=False).evidence
            truth = labels[row["case_id"]]["positive_target"]
            conclusion = row["conclusion"]
            if (row["tenant_id"] != case["tenant_id"]
                    or row["source_id"] != case["source_id"]
                    or target not in case["allowed_targets"]
                    or row["structural_evidence_id"] != str(structural.evidence_id)
                    or row["structural_sha256"] != _digest(structural.payload)
                    or conclusion["evidence_id"] != str(acquired.evidence_id)
                    or conclusion["source_record_ref"] != record["record_ref"]
                    or conclusion["record_sha256"] != _digest(record)):
                raise ValueError("source-to-conclusion reference mismatch")
            edges = Counter(field["options"] for field in case["fields"]
                            if field["fieldtype"] in {"Link", "Table", "Table MultiSelect"})
            total_edges += sum(edges.values())
            selected_edges += edges[target]
            domains[row["domain"]]["resolved_edges"] += edges[target]
            positive_hit = truth is not None and target == truth
            if truth is not None:
                domains[row["domain"]]["tp" if positive_hit else "fn"] += 1
            else:
                negative_cases += 1
            justified_abstention = (conclusion["status"] == "abstained"
                                    and (record["reference_unit_cost"] is None
                                         or record["approved_exception"]
                                         or record["currency"] != "USD"
                                         or record["unit"] != "each"))
            correct_abstentions += int(justified_abstention)
            details.append({
                "case_id": row["case_id"], "domain": row["domain"],
                "selected_target": target, "positive_target": truth,
                "source_record_ref": record["record_ref"],
                "source_sha256": _digest(record),
                "status": conclusion["status"],
                "positive_hit": positive_hit,
                "structural_edges_resolved": edges[target],
                "structural_edges_total": sum(edges.values()),
                "justified_abstention": justified_abstention,
                "failure_stage": ("prioritization" if truth is not None and not positive_hit
                                  else None),
            })
        methods[method] = {
            **scored["scores"][method],
            "structural_edges_resolved": selected_edges,
            "structural_edges_total": total_edges,
            "structural_information_gain_proxy": selected_edges / total_edges,
            "justified_abstentions": correct_abstentions,
            "negative_cases": negative_cases,
            "transfer_by_domain": dict(sorted(domains.items())),
            "cases": details,
        }
    return {
        "schema": "orion.study-selection-transfer-analysis.v1",
        "cases_sha256": hashlib.sha256(cases_path.read_bytes()).hexdigest(),
        "labels_sha256": hashlib.sha256(labels_path.read_bytes()).hexdigest(),
        "predictions_sha256": hashlib.sha256(predictions_path.read_bytes()).hexdigest(),
        "information_gain_definition": (
            "observed relationship fields resolved by the selected metadata read "
            "divided by all observed relationship fields; structural proxy only"
        ),
        "methods": methods,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(
        analyze(args.cases, args.labels, args.predictions), indent=2, sort_keys=True,
    ) + "\n")
