# Adaptive native intelligence: sealed synthetic evaluation

## Scope and sequence

PR #282 head `e9fc57f88b3af8a88d71bd8bfe6cccb4e8854c9b` is the baseline.
The post-seal failure diagnosis was committed at `7f7bb32`; the selector,
generator, tests and preregistration were frozen at `0df9722` before the new
cohorts were generated or scored. Original PR #282 holdout/transfer cases,
labels, calibration and predictions were not changed. Prediction files for
`unseen_a/b/c` were written and SHA-256 recorded before labels were opened
for scoring. No policy or threshold was retuned after seeing unseen scores.

The new policy ranks authorized questions by expected reduction in the
existing candidate/abstain decision loss. It reuses the same two-read
governed acquisition, training-only calibration, source digest checks and
report construction as the three comparators. It is adaptive to evidence
observed during a run. It is **not** an outcome-updated policy in this test.

## Evaluation

Each cohort has 72 synthetic organizations, three unfamiliar domain labels,
and separate designed synthetic labels. The three cohorts change signal
strength, prevalence and missingness. This is seeded synthetic generation by
the development team, not independent customer ground truth. All four methods
receive 432 reads and 432 units of evidence cost over 216 organizations.
`candidate_review` is a request for human review, not a validated finding.

| Method | Designed TP | FP | FN | Candidate queue | Correct abstentions | Information gain bits/read | Brier | Compute ms total |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Existing ORION | 23 | 4 | 56 | 27 | 100 | 0.109 | 0.177 | 708 |
| Enhanced ORION | 39 | 6 | 40 | 45 | 102 | 0.137 | 0.168 | 737 |
| Strong heuristic | 39 | 6 | 40 | 45 | 85 | 0.164 | 0.151 | 687 |
| Adaptive hybrid | 43 | 11 | 36 | 54 | 76 | 0.181 | 0.162 | 783 |

The designed positive count is 79. The hybrid's aggregate designed-label
precision is 43/54 = 79.6%, versus 39/45 = 86.7% for the heuristic; recall
is 43/79 = 54.4%, versus 39/79 = 49.4%. Neither is an independently validated
discovery rate. All source/report checks passed; provenance completeness was
1.0 in the evaluator. Actual analyst minutes and economic value were not
measured; queue size is only an effort proxy. Compute timings are WSL-local
diagnostics, not stable-Linux latency qualification.

| Cohort | Designed positives | Heuristic TP/FP | Hybrid TP/FP | Paired utility difference |
| --- | ---: | ---: | ---: | ---: |
| unseen_a | 29 | 14/1 | 19/1 | +5 |
| unseen_b | 27 | 14/0 | 14/6 | -12 |
| unseen_c | 23 | 11/5 | 10/4 | +1 |

Preregistered utility is `TP - 2*FP`. Across 216 paired cases, hybrid minus
heuristic is -6, or -0.0278 per organization. A fixed-seed 10,000-resample
percentile bootstrap 95% interval is [-0.1019, 0.0417]. The hybrid fails the
preregistered positive lower bound and no-worse-FP requirements. The strong
heuristic remains preferable under this evidence. Extra information gain did
not translate into better review economics. In `unseen_b`, the hybrid added
six false review candidates while finding no extra designed positives.

## Outcome update gate

`StudyStrategyVersion` and `derive_adjudicated_strategy` define an immutable
parent-linked calibration update. The gate requires at least 20 resolved
prospective outcomes, five of each class, evidence references from the
committed prediction, source and outcome custody verification, and an external reviewer
attestation distinct from the producer. A prediction must name the prior
strategy version; replay, future outcome, stale model, foreign tenant and
failed verification are denied. Tests use explicitly synthetic verifier mocks.

No independent procurement or finance reviewer is appointed. Therefore no
outcome-derived strategy was activated, no real held-out post-update test was
run, and outcome-learning improvement is **NOT_PROVEN**. The gate is a
proposal-only component. Trusted reviewer and source verifiers must be wired
to an approved custody service before it can be promoted. The local
prediction ledger checks chronology and identity but is not a tamper-proof
audit service.

## Conclusion and next falsifiable experiment

`INCREMENTAL_VALUE_BEYOND_HEURISTIC = NOT_PROVEN`. Keep the strong heuristic
as the research default. The next experiment should register, before data
collection, a drift detector that abstains when observed likelihoods differ
from training calibration. Compare it with the unchanged heuristic on fresh
organizations whose schemas and observations are generated independently;
hold both to the same two-read budget, human review, and no-worse-FP gate.
Only independently adjudicated prospective outcomes may feed an update, and
the updated version must beat its frozen parent on later unseen organizations.
