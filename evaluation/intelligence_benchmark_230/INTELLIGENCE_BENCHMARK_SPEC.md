# Intelligence benchmark specification (issue #230)

Base source: PR #228 head `2c7fe3ea34cbb2258f03854b2002884214a76d9e`.
Run `python3 evaluation/intelligence_benchmark_230/generate.py`, then
`PYTHONPATH=.:src python3 evaluation/intelligence_benchmark_230/evaluate.py`.
The generator must reproduce the committed `events.json` and
`ground_truth.json` byte for byte. The evaluator reads the event evidence,
freezes detector outputs, and only then opens the separate label file.

The fixed fixture has 72 rows, two synthetic organizations, three periods,
three categories, four rows per category and period, and eight positive
tenant-period-anomaly units. Calibration is periods 0–1; period 2 is held out
for price statistics and naive demand forecasting. Cases cover unit-normalized
supplier price variance, duplicate and incorrect invoices, inventory mismatch,
margin deterioration, missing and contradictory evidence, an approved-promotion
false-positive trap, cross-tenant invoice identity, and a superseded stock
record. A correction invalidates the missing-document candidate. The same
72-row evidence budget is visible to the conventional detector and ORION;
ground-truth labels and future outcomes are never passed to either.

The conventional detector implements seven transparent rules. A median and
median-absolute-deviation price rule uses calibration periods only. The naive
forecast uses previous-period demand by tenant and category. ORION is invoked
only through its real retained missing-value investigation with a tenant-bound
authorization envelope. The raw transaction fixture lacks the independent
semantic witnesses required for ORION's F&B sample assessment, so that feature
is **not** represented as an invoice, inventory, or margin detector here.

The labels are deterministic **synthetic design labels**, separate from
detectors but not independent human ground truth. They are not customer facts.
The fixture is small, injected anomalies are partly simple, and no confidence
interval supports commercial generalization. A blind external reviewer and
authorized customer baseline remain mandatory before value claims.
