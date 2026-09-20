# Issue #187 requirement-to-evidence matrix

| Requirement | Implementation | Executable evidence | Status |
|---|---|---|---|
| Preserve v1 bytes and validation | Existing `frozen_learning_v1` files are unchanged; strict dispatcher retains the v1 validator | `test_v1_contract_bytes_and_validation_remain_unchanged` plus existing v1 test file | SATISFIED |
| Separate synthetic and real clocks | V2 staged-material validation checks only cutoff/start/releases; receipt/review/run validators check only real timestamps | `test_v2_separates_historical_synthetic_time_from_later_real_receipt` | SATISFIED |
| Honest unknown preparation history | V2 requires `UNKNOWN` + `null` + reason, or an aware known timestamp | `test_unknown_original_preparation_forbids_a_timestamp` | SATISFIED |
| Exact raw, material, oracle and disclosure binding | Freeze receipt binds raw package SHA-256, canonical projection, sealed commitment and disclosure/generation files without opening the oracle | `test_receipt_rejects_modified_material_or_commitment` | SATISFIED |
| Non-circular independent review envelope | Review binds the receipt, package/material, preparer, reviewer, actual review time and scope, but cannot authorize execution by itself | chronology, approval and mismatch tests in `test_frozen_learning_chronology.py` | SATISFIED WITHIN SYNTHETIC CONTRACT TESTS |
| Fail closed before source I/O | V2 execution validates receipt/review chronology, requires a separately trusted exact-review-digest approval and separately trusted exact authorization set, then durably records run start before discovery | `test_missing_or_mismatched_receipt_and_review_chronology_deny_before_source_io` and authorization-boundary tests | SATISFIED |
| Strict version dispatch | Only matching v1/v1 or v2/v2 pairs dispatch | `test_mixed_or_unsupported_versions_fail_closed` | SATISFIED |
| Traceable conversion | Conversion retains original digest/authorship, creates a new envelope/report and verifies identical staged material and sealed commitment | `test_conversion_preserves_operational_material_and_oracle_and_retains_original` | SATISFIED |
| Prediction-before-outcome and unchanged learner | Existing staged release gate retained; frozen component map exactly equals v1 | existing release-gate tests and `test_v2_keeps_frozen_learner_and_scoring_identity` | SATISFIED |
| Real reviewer identity, independence and trusted time | Explicitly outside digest/local-clock proof; trusted approval is a required controller input, not proof supplied by the review JSON | Separate human/external evidence and approval bound to the exact review artifact | NOT PROVEN; correctly not claimed |
| External evaluation result | Prohibited in issue #187 | External archive integrity/conformance inspection only; nonconforming material receives no receipt/review, sealed answers are not parsed, and no learner execution occurs | NOT RUN |

Candidate-host qualification remains unresolved. No release status or gate is
promoted. `execution_allowed=false`, `allow_live_customer_access=false`, and
`LIVE_PILOT_READY=false`.
