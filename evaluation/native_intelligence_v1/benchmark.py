"""Equal-access offline question selection; holdout labels are not imported."""

from __future__ import annotations

import hashlib
import json
import math
import random
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from orion.contracts import Evidence, EvidenceKind, Observation, ObservationMode
from orion.discovery.governed_runner import run_governed_discovery
from orion.discovery.planner import (
    DiscoveryAuthorization,
    DiscoveryPlan,
    DiscoveryPlanItem,
    DiscoveryTargetKind,
)
from orion.learning.epistemic_selection import (
    QuestionModel,
    eligible_questions,
    posterior,
    rank_authorized_questions,
)
from orion.understanding.metadata import MetadataUnderstanding, StructuralEntity, StructuralField

METHODS = ("existing", "enhanced", "fixed", "random", "heuristic",
           "learning_progress", "frozen_nonlearning")
ANSWERS = ("flag", "clear", "missing")
AT = datetime(2026, 3, 2, tzinfo=UTC)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _structural_id(case_id: str, target: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"native-lab:{case_id}:{target}:structure")


def _record_id(case_id: str, target: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"native-lab:{case_id}:{target}:record")


def _run_id(case_id: str, method: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"native-lab:{case_id}:{method}:run"))


def _report_digest(case_id: str, method: str, status: str,
                   probability: float, acquisitions: list[dict]) -> str:
    return _digest({
        "run_id": _run_id(case_id, method), "status": status,
        "posterior_issue_probability": probability,
        "evidence": [(item["evidence_id"], item["source_record_sha256"])
                     for item in acquisitions],
    })


def calibrate(training_cases_path: Path, training_labels_path: Path) -> dict:
    """Estimate likelihoods from training outcomes and checked source digests."""
    cases = json.loads(training_cases_path.read_bytes())
    labels = json.loads(training_labels_path.read_bytes())
    if cases.get("split") != "training" or labels.get("split") != "training":
        raise ValueError("calibration accepts training data only")
    case_ids = {case["case_id"] for case in cases["cases"]}
    if set(labels["labels"]) != case_ids:
        raise ValueError("training outcomes are incomplete")
    counts = defaultdict(lambda: [[1, 1, 1], [1, 1, 1]])  # Laplace smoothing.
    identities = defaultdict(list)
    issues = 1
    for case in cases["cases"]:
        issue = labels["labels"][case["case_id"]]["issue"]
        if type(issue) is not bool:
            raise ValueError("training outcome must be boolean")
        issues += int(issue)
        for question in case["questions"]:
            target = question["target"]
            evidence = case["records"][target]
            if _digest(evidence["record"]) != evidence["sha256"]:
                raise ValueError("training record digest mismatch")
            answer = evidence["record"]["answer"]
            if answer not in ANSWERS:
                raise ValueError("training answer is invalid")
            feature = question["feature"]
            counts[feature][int(issue)][ANSWERS.index(answer)] += 1
            identities[feature].append(str(_record_id(case["case_id"], target)))
    features = {}
    for feature, rows in sorted(counts.items()):
        features[feature] = {
            "likelihoods": [[value / sum(row) for value in row] for row in rows],
            "evidence_ids": sorted(set(identities[feature])),
        }
    return {
        "schema": "orion.native-lab-calibration.v1",
        "training_cases_sha256": hashlib.sha256(training_cases_path.read_bytes()).hexdigest(),
        "training_labels_sha256": hashlib.sha256(training_labels_path.read_bytes()).hexdigest(),
        "prior": [1 - issues / (len(cases["cases"]) + 2),
                  issues / (len(cases["cases"]) + 2)],
        "features": features,
    }


def _understanding(case: dict) -> MetadataUnderstanding:
    root_fields = tuple(StructuralField(
        doctype=case["seed_doctype"], fieldname=f"edge_{index}_{copy}",
        fieldtype=question["fieldtype"], label=None, options=question["target"],
        required=False, read_only=True, hidden=False, unique=False,
    ) for index, question in enumerate(case["questions"]) if question["visible"]
        for copy in range(question["relationship_count"]))
    root = StructuralEntity(case["seed_doctype"], None, False, False, False,
                            root_fields, (_structural_id(case["case_id"], case["seed_doctype"]),))
    targets = tuple(StructuralEntity(
        question["target"], None, False, False, False, (),
        (_structural_id(case["case_id"], question["target"]),),
    ) for question in case["questions"] if question["visible"])
    return MetadataUnderstanding(case["tenant_id"], (root, *targets))


def _questions(case: dict, calibration: dict) -> tuple[QuestionModel, ...]:
    return tuple(QuestionModel(
        tenant_id=case["tenant_id"], question_id=question["question_id"],
        target=question["target"],
        provenance_ids=(_structural_id(case["case_id"], question["target"]),),
        answers=ANSWERS,
        likelihoods=tuple(tuple(row) for row in calibration["features"][question["feature"]]["likelihoods"]),
        calibration_evidence_ids=tuple(UUID(value) for value in
                                       calibration["features"][question["feature"]]["evidence_ids"]),
        request_cost=question["request_cost"], privacy_risk=question["privacy_risk"],
        operational_risk=question["operational_risk"], relevance=question["relevance"],
    ) for question in case["questions"] if question["visible"] and question["permitted"])


def _observation(case: dict, target: str) -> Observation:
    record = case["records"][target]
    evidence = Evidence(
        kind=EvidenceKind.API, source=case["source_id"],
        tenant_id=case["tenant_id"],
        payload={"resource": target, "record": record["record"],
                 "record_sha256": record["sha256"]}, observed_at=AT,
        evidence_id=_record_id(case["case_id"], target),
    )
    return Observation(evidence=evidence, mode=ObservationMode.READ_ONLY,
                       observation_id=uuid5(NAMESPACE_URL, str(evidence.evidence_id) + ":obs"))


def _choose(method: str, candidates: tuple[QuestionModel, ...], case: dict,
            belief: tuple[float, ...], calibration: dict,
            understanding: MetadataUnderstanding,
            authorization: DiscoveryAuthorization) -> tuple[QuestionModel, str]:
    by_id = {question.question_id: question for question in candidates}
    verifier = {UUID(value) for feature in calibration["features"].values()
                for value in feature["evidence_ids"]}
    if method == "enhanced":
        ranked = rank_authorized_questions(
            belief, candidates, understanding=understanding, authorization=authorization,
            calibration_verifier=lambda identity: identity in verifier,
            max_request_cost=1, max_privacy_risk=0.2, max_operational_risk=0.2,
        )
        if not ranked:
            raise ValueError("no authorized question remains")
        selected = ranked[0]
        return by_id[selected.question_id], selected.rationale
    candidates = tuple(question for question, _ in eligible_questions(
        candidates, understanding=understanding, authorization=authorization,
        calibration_verifier=lambda identity: identity in verifier,
        max_request_cost=1, max_privacy_risk=0.2, max_operational_risk=0.2,
    ))
    if not candidates:
        raise ValueError("no authorized question remains")
    if method in {"existing", "frozen_nonlearning"}:
        selected = min(candidates, key=lambda question: question.target)
    elif method == "fixed":
        selected = candidates[0]
    elif method == "random":
        seed = int(hashlib.sha256((case["case_id"] + ":random").encode()).hexdigest(), 16)
        selected = random.Random(seed).choice(candidates)
    elif method == "heuristic":
        selected = max(candidates, key=lambda question: (
            abs(question.likelihoods[1][0] - question.likelihoods[0][0])
            / question.request_cost, -len(question.target), question.question_id,
        ))
    elif method == "learning_progress":
        selected = max(candidates, key=lambda question: (
            # Training-only learning progress proxy: usable calibration volume.
            len(question.calibration_evidence_ids) / question.request_cost,
            question.question_id,
        ))
    else:
        raise ValueError("unknown method")
    return selected, f"{method} selection under common governed preflight"


def run_case(case: dict, calibration: dict, method: str) -> dict:
    started = time.perf_counter_ns()
    understanding = _understanding(case)
    questions = _questions(case, calibration)
    authorization = DiscoveryAuthorization(
        case["tenant_id"], record_targets=frozenset(question.target for question in questions),
        max_targets=1,
    )
    belief = tuple(calibration["prior"])
    prior_entropy = -sum(value * math.log2(value) for value in belief if value)
    selected_ids = set()
    acquisitions = []
    shifts = []
    for _ in range(2):
        remaining = tuple(question for question in questions
                          if question.question_id not in selected_ids)
        question, rationale = _choose(method, remaining, case, belief, calibration,
                                      understanding, authorization)
        plan = DiscoveryPlan(case["tenant_id"], (DiscoveryPlanItem(
            DiscoveryTargetKind.RECORDS, question.target, rationale,
            question.provenance_ids,
        ),))
        reads = []

        def reader(target: str, reads: list[str] = reads) -> tuple[Observation, ...]:
            reads.append(target)
            return (_observation(case, target),)

        observation = run_governed_discovery(
            plan, authorization=authorization, understanding=understanding,
            metadata_reader=lambda _: (), record_reader=reader,
        ).observations[0]
        if reads != [question.target]:
            raise ValueError("governed read deviated from selected target")
        evidence = observation.evidence
        record = evidence.payload["record"]
        if (_digest(record) != evidence.payload["record_sha256"]
                or evidence.tenant_id != case["tenant_id"]
                or evidence.source != case["source_id"]):
            raise ValueError("acquired evidence identity or digest mismatch")
        previous = belief
        belief = posterior(belief, question, record["answer"])
        shifts.append(belief[1] - previous[1])
        acquisitions.append({
            "question_id": question.question_id, "target": question.target,
            "feature": next(q["feature"] for q in case["questions"]
                            if q["question_id"] == question.question_id),
            "rationale": rationale, "answer": record["answer"],
            "source_record_ref": record["record_ref"],
            "source_record_sha256": evidence.payload["record_sha256"],
            "evidence_id": str(evidence.evidence_id),
            "acquired_at": evidence.observed_at.isoformat(),
            "source_id": evidence.source, "tenant_id": evidence.tenant_id,
        })
        selected_ids.add(question.question_id)
    contradiction = any(value > 1e-9 for value in shifts) and any(value < -1e-9
                                                                   for value in shifts)
    status = ("abstained" if contradiction or 0.2 < belief[1] < 0.8
              else "candidate_review" if belief[1] >= 0.8 else "no_anomaly")
    entropy = -sum(value * math.log2(value) for value in belief if value)
    return {
        "case_id": case["case_id"], "domain": case["domain"], "method": method,
        "run_id": _run_id(case["case_id"], method),
        "tenant_id": case["tenant_id"], "source_id": case["source_id"],
        "prior_issue_probability": calibration["prior"][1],
        "posterior_issue_probability": belief[1],
        "realized_information_gain_bits": prior_entropy - entropy,
        "status": status, "contradiction": contradiction,
        "report_sha256": _report_digest(case["case_id"], method, status,
                                        belief[1], acquisitions),
        "alternative_explanations": ["conflicting authorized observations"] if contradiction else [],
        "missing_evidence": ["additional independent observation"] if status == "abstained" else [],
        "acquisitions": acquisitions, "request_count": len(acquisitions),
        "evidence_cost": sum(next(q["request_cost"] for q in case["questions"]
                                  if q["question_id"] == item["question_id"])
                             for item in acquisitions),
        "compute_ns": time.perf_counter_ns() - started,
        "read_only": True, "execution_allowed": False,
    }


def run_benchmark(cases_path: Path, calibration_path: Path) -> dict:
    source = cases_path.read_bytes()
    cases = json.loads(source)
    calibration = json.loads(calibration_path.read_bytes())
    if cases.get("schema") != "orion.native-lab-cases.v1" or cases.get("split") == "training":
        raise ValueError("evaluation cases required")
    if calibration.get("schema") != "orion.native-lab-calibration.v1":
        raise ValueError("sealed training calibration required")
    return {
        "schema": "orion.native-lab-predictions.v1", "split": cases["split"],
        "cases_sha256": hashlib.sha256(source).hexdigest(),
        "calibration_sha256": hashlib.sha256(calibration_path.read_bytes()).hexdigest(),
        "method_order": list(METHODS), "labels_available_to_selector": False,
        "predictions": [run_case(case, calibration, method)
                        for case in cases["cases"] for method in METHODS],
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(
        run_benchmark(args.cases, args.calibration), sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ) + "\n")
