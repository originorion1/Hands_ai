# Native-intelligence benchmark

The frozen, equal-access lab compares existing ORION planner record order, finite Bayesian selection (`enhanced`), fixed order, deterministic random, strong training-calibrated heuristic, a calibration-volume learning-progress proxy, and frozen nonlearning ORION. All methods see the same structural model and verified training calibration, have two read-only governed source requests per case, and use the same posterior/update/abstention rule. This isolates **question selection**; it does not compare full distinct reasoning products. Holdout and transfer each have 72 cases. Labels were scored only after prediction artifact digests were sealed. There is one independent seed per split, not multiple independent generator families.

| Split | Method | TP | FP | FN | Precision | Recall | Correct abstentions | Bits/request | Brier |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Holdout | Existing | 7 | 2 | 14 | 0.778 | 0.333 | 33 | 0.112 | 0.146 |
| Holdout | Enhanced | 11 | 0 | 10 | 1.000 | 0.524 | 20 | 0.185 | 0.091 |
| Holdout | Fixed | 8 | 1 | 13 | 0.889 | 0.381 | see score JSON | see score JSON | see score JSON |
| Holdout | Random | 10 | 2 | 11 | 0.833 | 0.476 | see score JSON | see score JSON | see score JSON |
| Holdout | Strong heuristic | 11 | 0 | 10 | 1.000 | 0.524 | 31 | 0.154 | 0.109 |
| Transfer | Existing | 8 | 4 | 19 | 0.667 | 0.296 | 26 | 0.126 | 0.266 |
| Transfer | Enhanced | 10 | 4 | 17 | 0.714 | 0.370 | 38 | 0.123 | 0.198 |
| Transfer | Strong heuristic | 10 | 4 | 17 | 0.714 | 0.370 | 37 | 0.133 | 0.218 |

`frozen_nonlearning` is identical to existing; `learning_progress` is only a proxy and not learned outcome improvement. The "correct abstentions" column counts abstentions justified by contradiction or wholly missing evidence under the prespecified rule; it is not an independently adjudicated correctness judgment. Full seven-method metrics, domain slices, Wilson intervals, source/evidence coverage, request cost, compute milliseconds, and provenance checks are in `holdout-score.json` and `transfer-score.json` in the external evidence directory. Every method made 144 requests per split. Provenance completeness in the scorer is 1.0 **conditional on its synthetic source and report-digest checks**; this is not a custody audit of a deployed service. Analyst minutes, independent discoveries, and verified net economic value are null because they were not measured. No financial amounts exist in this lab. Compute timings are host dependent and exclude analyst review.

Paired case bootstrap (5,000 resamples) for enhanced minus existing on holdout gives Brier reduction 0.0126–0.1017 and information-gain increase 0.0867–0.2074 bits/case; designed-hit difference interval −0.0139–0.1389 and exact discordant p=0.289. Versus the strong heuristic, designed-hit difference is exactly zero. Transfer gains are less stable; four false positives remain. These intervals condition on this generator and omit uncertainty from independent environments, outcomes, and review costs. **NATIVE_INTELLIGENCE_IMPROVEMENT = INCONCLUSIVE** and no economic value is established.
