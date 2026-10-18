# Cross-domain transfer results

Training used fleet, facilities and billing vocabulary. The held-out split used mobility, utilities and service; the transfer split used manufacturing, energy and distribution with changed F0 and F3 observation rates. Policy and training calibration were frozen before both splits. Each split contains 72 synthetic organizations and uses two authorized reads per method.

| Split | Method | Designed TP / positives | FP | Precision | Recall | Information gain bits/read | Brier |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Holdout | Existing | 7 / 21 | 2 | 0.778 | 0.333 | 0.112 | 0.146 |
| Holdout | Enhanced | 11 / 21 | 0 | 1.000 | 0.524 | 0.185 | 0.091 |
| Holdout | Strong heuristic | 11 / 21 | 0 | 1.000 | 0.524 | 0.154 | 0.109 |
| Transfer | Existing | 8 / 27 | 4 | 0.667 | 0.296 | 0.126 | 0.266 |
| Transfer | Enhanced | 10 / 27 | 4 | 0.714 | 0.370 | 0.123 | 0.198 |
| Transfer | Strong heuristic | 10 / 27 | 4 | 0.714 | 0.370 | 0.133 | 0.218 |

The transfer false-positive cases for enhanced are `TRANSFER-000`, `TRANSFER-012`, `TRANSFER-041`, and `TRANSFER-054`. A training likelihood model overweights flags when transfer rates change. The strong heuristic ties the enhanced policy on discoveries and false positives in both splits. The paired transfer enhanced-minus-existing designed-hit rate interval is −0.0417 to 0.1111; the information-gain interval is −0.0666 to 0.0607 bits/case. This experiment does not demonstrate robust strategy transfer under distribution shift. **CROSS_DOMAIN_TRANSFER = NOT_PROVEN**.

These are designed synthetic labels, not independently adjudicated outcomes. Different domain words do not establish transfer across real business models. Next test must use independent generator families and blinded domain experts, with a preregistered rule for detecting calibration shift and abstaining before false-positive cost is incurred.
