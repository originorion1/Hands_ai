# Conventional baseline versus actual ORION capability

Reproducible output: `results.json`; unit: tenant-period-anomaly class, with
42 possible units and eight synthetic design positives. The generator checks
72 unique evidence IDs. All eight labels are tied to fixture row IDs.

| Detector | TP | FP | FN | Precision | Recall | Meaning |
|---|---:|---:|---:|---:|---:|---|
| Seven conventional rules | 8 | 1 | 0 | 0.889 | 1.000 | One approved promotion is flagged by the simple margin rule. |
| ORION retained missing-value investigation | 1 | 0 | 7 | 1.000 | 0.125 | One supported missing-document class; other six classes are outside this investigation's scope. |
| Statistical price rule, held-out price units only | 1 | 0 | 0 | 1.000 | 1.000 | One positive and one negative; far too small for a performance claim. |

The conventional false-positive rate is 1/34 = 0.0294; ORION's is 0/34 on
this fixture. ORION has **zero incremental validated discoveries** above the
conventional baseline. Its single finding has 12 retained evidence records,
a relevant-evidence digest, and a source-provenance digest. Cross-tenant
metadata and absent authorization were denied. A corrected record cleared
the stale missing-document candidate. The other anomaly classes were not
silently attributed to ORION.

The naive previous-period demand baseline has mean absolute error 4 units
across six held-out tenant/category groups. There is no comparable ORION
forecast in this fixture. Candidate review effort uses an explicit six-minute
assumption; actual analyst minutes and detection delay were not observed.
Information gain per evidence request is not measurable because the fixture
provides a fixed batch and no adaptive evidence requests. ORION's finding
does not carry calibrated probability; its precision here is an empirical
fixture ratio, not a confidence estimate. The sample is too small for
statistical significance.
