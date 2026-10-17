# Offline hybrid candidate, issue #237

Reference source: PR #232 `ef67bfe3a6c3ab41391c208957deb0d7d4842c49`.
The evaluator composes the existing deterministic rules, price statistic and
real ORION missing-evidence investigation by set union. It adds no runtime
detector, library, connector, network request or execution authority. The
original 72-row evidence and design-label files are checked by SHA-256 before
use. Predictions are frozen in memory before the separate labels are opened.
All methods see the same tenant-period evidence universe. ORION provenance
remains attached to its one applicable finding. Other anomaly classes remain
unsupported by the current ORION investigation, even though deterministic
controls detect them.

Run `python -m evaluation.hybrid_237.generate_active` to reproduce the active
fixtures and `python -m evaluation.hybrid_237.evaluate` to reproduce results.
All data are synthetic and cannot authorize customer access.
