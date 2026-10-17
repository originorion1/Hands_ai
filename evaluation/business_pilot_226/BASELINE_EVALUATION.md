# Offline baseline evaluation

Canonical source `eabda90593f00ddc9b6ace6a4b9b9301fb3a01dd`. This is a deterministic **synthetic** comparison, not a customer ERP report or customer outcome study. Fixture SHA-256: `da848620c31ea06ad2b9ccf01df3db5576030f1367ba7c954c57c017ec525495`. Result SHA-256: `757bc6103580157ceea378c738a65c41742be7bb26e14f02a67bc828d17280d4`.

Run from the repository root with Python 3.12:

```bash
PYTHONPATH=src python3 evaluation/business_pilot_226/evaluate.py > /tmp/orion-pilot-226-result.json
sha256sum /tmp/orion-pilot-226-result.json
cmp /tmp/orion-pilot-226-result.json evaluation/business_pilot_226/result.json
```

The conventional baseline scans each authorized field independently and raises a period-field review candidate if any record contains `null` or a blank string. ORION independently runs `investigate_retained_missing_values` on versioned, tenant-scoped historical batches. The synthetic fixture preregisters review labels before either method runs: calibration `cost_center` merits review; `project` is an expected exception in both periods. These labels are designed test annotations, not domain-analyst adjudications.

| Measure across four period-field units | Conventional baseline | ORION first candidate |
| --- | ---: | ---: |
| True positives | 1 | 1 |
| False positives | 2 | 1 |
| False negatives | 0 | 0 |
| True negatives | 1 | 2 |
| Precision | 1/3 | 1/2 |
| False-positive rate | 2/3 | 1/3 |
| Recall | 1 | 1 |
| Validated incremental discoveries absent from baseline | — | 0 |

The calibration window is `2026-01-01` to `2026-01-31` (four synthetic Purchase Invoices); held-out is `2026-02-01` to `2026-02-28` (four). No threshold was fitted to held-out data. The held-out ORION candidate is a synthetic false-positive review ticket; `applicability` remains `unknown` and `business_defect_validated=false`. A later synthetic correction removes that candidate. With one synthetic positive and three negatives, these ratios have extreme sampling uncertainty and cannot support a customer precision or business-value claim.

The actual customer's existing report name, filters, refresh time, cost, and historical outputs are unknown. Owner review must freeze those artifacts and blind the analyst adjudication before any customer comparison. No procurement or inventory learning improvement was measured; `heldout_learning_improvement` is `null` in the result. Inventory records were not evaluated.
