# Issue #275 three-way procurement evaluation

## Frozen inputs and comparison

The twelve synthetic source cases and their 48 rows were committed before this issue's hybrid method was written. The hybrid policy was fixed before opening the label file in the benchmark process. All three arms receive the same complete rows, period (2026-01-01 through 2026-03-01), read scope and 15% threshold. No arm receives the label file, future outcomes or extra customer evidence. The separate `ground_truth.json` contains designed synthetic labels; it is not independent adjudication.

- **Conventional union:** contract threshold, historical mean/median, supplier comparison, and duplicate/unit rule. Every flag is retained for analyst review.
- **Existing ORION:** the issue #270 deterministic investigation with quantity, tax, discount and FX normalization, comparable invoice selection, exclusions, alternatives, missing evidence, bounded arithmetic and abstention.
- **Hybrid:** run the conventional union as candidate selection, then apply the same evidence-grounded ORION investigation to flagged cases. A case that conventional rules do not flag is not investigated. This constraint permits the hybrid to lose a true positive and cannot grant it more evidence.

`python -m evaluation.issue_275.benchmark` first records all predictions, then opens the label file. `python -m evaluation.issue_275.blind_packet` exports source rows without labels or detector conclusions. Hashes in `three_way_results.json` bind the rows and label file. A reviewer should use `blinded_cases.json` and supply a separate signed decision for each case before seeing scores.

## Observed synthetic result

| Arm | TP | FP | FN | Precision | Recall | Correct negative abstentions | Assumed review minutes |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Conventional union | 3 | 6 | 0 | 0.333 | 1.000 | 0 | 54 |
| Existing ORION | 3 | 0 | 0 | 1.000 | 1.000 | 3 | 18 |
| Hybrid investigation | 3 | 0 | 0 | 1.000 | 1.000 | 3 | 18 |

The hybrid produces **zero** new designed positive conclusions beyond either comparator. ORION and hybrid precision are based on three predicted positives; the Wilson 95% interval is about 0.438–1.000. The 36-minute apparent review reduction uses an assumed six minutes per flagged case and is not a measured analyst outcome. No probability calibration can be calculated from categorical confidence. The benchmark reports local diagnostic compute time and twelve logical synthetic evidence batches per arm, but no billed compute or acquisition cost. USD 190 is estimated exposure across synthetic candidates; verified avoidable cost, realized savings and net economic value are unknown.

## Independent adjudication and next falsifiable experiment

No independent reviewer has adjudicated these cases yet. The author-defined labels must not be described as verified customer outcomes. An independent procurement or finance reviewer should receive only the blinded packet, record source references, alternative explanations, calculation, decision and actual review minutes, then seal decisions before opening the existing labels or scores. A second reviewer should resolve disagreements. The predeclared primary comparison is incremental reviewer-validated useful findings or measured review minutes at equal evidence access, net of review and request/compute costs. The conventional union is allowed to win. If the hybrid adds no validated finding and saves no measured net review effort, the value hypothesis is falsified for this sample; do not tune thresholds on this holdout.

The frozen twelve-case sample is too small and too design-derived for a representative 80% precision claim. A later evaluation needs independently authored, larger held-out source records with a pre-registered period and adjudication process. Customer access requires separate authorization and is not part of this benchmark.
