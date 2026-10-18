# PR #282 sealed-case failure diagnosis

Analysis is post-seal and evaluator-only. The original holdout and transfer cases, labels, calibration, and predictions remain unchanged. A post-hoc two-read oracle inspects all authorized source answers to ask whether **any** pair would satisfy the frozen conclusion rule; it is an upper bound, not information available to a selector. Every row below is a designed synthetic label, not an independently reviewed outcome. No selected acquisition exceeded its two-read authorization or lost source/report lineage.

Classification: `priority` means a qualifying authorized pair existed but enhanced did not select it; `insufficient` means no two-read pair qualified under the frozen threshold and contradiction rule; `shift/noise` means a negative was flagged under training likelihoods in the shifted transfer set. A `priority` label does not imply that a selector could identify the pair before reading it.

| Split | Case | Designed label | Enhanced evidence (feature:answer; record refs) | Conclusion | Post-hoc qualifying pairs | Root cause |
| --- | --- | --- | --- | --- | ---: | --- |
| holdout | `HOLDOUT-006` | 1 | F0:clear HOLDOUT-006-R2, F3:clear HOLDOUT-006-R5 | abstained (0.496) | 3 | priority |
| holdout | `HOLDOUT-011` | 1 | F0:clear HOLDOUT-011-R4, F3:flag HOLDOUT-011-R2 | no_anomaly (0.108) | 0 | insufficient |
| holdout | `HOLDOUT-018` | 1 | F0:flag HOLDOUT-018-R3, F1:clear HOLDOUT-018-R1 | abstained (0.539) | 1 | priority |
| holdout | `HOLDOUT-021` | 1 | F0:flag HOLDOUT-021-R4, F1:clear HOLDOUT-021-R1 | abstained (0.539) | 0 | insufficient |
| holdout | `HOLDOUT-024` | 1 | F0:flag HOLDOUT-024-R4, F1:clear HOLDOUT-024-R3 | abstained (0.539) | 3 | priority |
| holdout | `HOLDOUT-035` | 1 | F3:clear HOLDOUT-035-R3, F4:missing HOLDOUT-035-R2 | abstained (0.671) | 0 | insufficient |
| holdout | `HOLDOUT-051` | 1 | F0:clear HOLDOUT-051-R5, F3:clear HOLDOUT-051-R4 | abstained (0.496) | 3 | priority |
| holdout | `HOLDOUT-054` | 1 | F0:clear HOLDOUT-054-R4, F3:clear HOLDOUT-054-R2 | abstained (0.496) | 3 | priority |
| holdout | `HOLDOUT-055` | 1 | F0:flag HOLDOUT-055-R2, F1:clear HOLDOUT-055-R1 | abstained (0.539) | 0 | insufficient |
| holdout | `HOLDOUT-069` | 1 | F0:flag HOLDOUT-069-R3, F1:clear HOLDOUT-069-R5 | abstained (0.539) | 1 | priority |
| transfer | `TRANSFER-000` | 0 | F1:flag TRANSFER-000-R1, F3:clear TRANSFER-000-R3 | candidate_review (0.891) | 1 | shift/noise |
| transfer | `TRANSFER-002` | 1 | F0:clear TRANSFER-002-R1, F3:clear TRANSFER-002-R5 | abstained (0.496) | 0 | insufficient |
| transfer | `TRANSFER-003` | 1 | F0:clear TRANSFER-003-R5, F3:flag TRANSFER-003-R1 | no_anomaly (0.108) | 1 | priority |
| transfer | `TRANSFER-009` | 1 | F0:clear TRANSFER-009-R1, F3:clear TRANSFER-009-R5 | abstained (0.496) | 1 | priority |
| transfer | `TRANSFER-012` | 0 | F0:flag TRANSFER-012-R2, F1:flag TRANSFER-012-R3 | candidate_review (0.905) | 3 | shift/noise |
| transfer | `TRANSFER-014` | 1 | F0:flag TRANSFER-014-R2, F1:clear TRANSFER-014-R3 | abstained (0.539) | 1 | priority |
| transfer | `TRANSFER-019` | 1 | F0:clear TRANSFER-019-R3, F3:clear TRANSFER-019-R1 | abstained (0.496) | 1 | priority |
| transfer | `TRANSFER-021` | 1 | F0:clear TRANSFER-021-R2, F1:flag TRANSFER-021-R1 | abstained (0.458) | 0 | insufficient |
| transfer | `TRANSFER-024` | 1 | F0:clear TRANSFER-024-R3, F3:clear TRANSFER-024-R2 | abstained (0.496) | 3 | priority |
| transfer | `TRANSFER-025` | 1 | F0:clear TRANSFER-025-R2, F3:clear TRANSFER-025-R5 | abstained (0.496) | 1 | priority |
| transfer | `TRANSFER-032` | 1 | F0:clear TRANSFER-032-R3, F3:clear TRANSFER-032-R1 | abstained (0.496) | 3 | priority |
| transfer | `TRANSFER-035` | 1 | F0:clear TRANSFER-035-R2, F3:clear TRANSFER-035-R3 | abstained (0.496) | 0 | insufficient |
| transfer | `TRANSFER-038` | 1 | F0:clear TRANSFER-038-R5, F3:clear TRANSFER-038-R3 | abstained (0.496) | 1 | priority |
| transfer | `TRANSFER-041` | 0 | F0:flag TRANSFER-041-R2, F1:flag TRANSFER-041-R5 | candidate_review (0.905) | 1 | shift/noise |
| transfer | `TRANSFER-047` | 1 | F0:flag TRANSFER-047-R5, F1:clear TRANSFER-047-R4 | abstained (0.539) | 0 | insufficient |
| transfer | `TRANSFER-054` | 0 | F0:flag TRANSFER-054-R5, F1:flag TRANSFER-054-R2 | candidate_review (0.905) | 3 | shift/noise |
| transfer | `TRANSFER-056` | 1 | F0:flag TRANSFER-056-R3, F1:clear TRANSFER-056-R4 | abstained (0.539) | 0 | insufficient |
| transfer | `TRANSFER-060` | 1 | F0:clear TRANSFER-060-R1, F3:clear TRANSFER-060-R3 | abstained (0.496) | 0 | insufficient |
| transfer | `TRANSFER-061` | 1 | F0:clear TRANSFER-061-R1, F3:clear TRANSFER-061-R3 | abstained (0.496) | 3 | priority |
| transfer | `TRANSFER-062` | 1 | F0:flag TRANSFER-062-R2, F1:clear TRANSFER-062-R4 | abstained (0.539) | 1 | priority |
| transfer | `TRANSFER-067` | 1 | F0:clear TRANSFER-067-R2, F3:clear TRANSFER-067-R1 | abstained (0.496) | 1 | priority |

Holdout: six priority misses, four insufficient-evidence misses, zero false positives. Transfer: eleven priority misses, six insufficient-evidence misses, four shift/noise false positives (`TRANSFER-000`, `-012`, `-041`, `-054`). The enhanced and strong-heuristic candidate sets are identical on both sealed splits despite occasional different reads. The four transfer false positives demonstrate that training likelihoods can overstate certainty after data distribution changes; no independent labels are available for a trusted update.

Root cause for the actionable research experiment: existing information gain optimizes average posterior entropy, whereas the decision is a fixed-budget, costly candidate/abstain choice. A predeclared decision-risk utility is worth comparing with the strong heuristic on **new** organizations. It must not use this post-hoc oracle or sealed labels for training, feature weights, thresholds, or per-case routing. Calibration shift needs independently adjudicated outcomes or defensible abstention; simply fitting these transfer labels would leak future information.

Exact diagnostic artifacts: `/home/orion/evidence/orion-adaptive-intelligence-v1/holdout-failure-analysis.json` and `transfer-failure-analysis.json`. Their source prediction hashes are `79af65905c20fea4fef45af3e28b8c453ddb519abca958e09e78e3984d2d68ee` and `074180cae4261c42119523fd255132994467bd08961992125409a4f4451fe2e7`.
