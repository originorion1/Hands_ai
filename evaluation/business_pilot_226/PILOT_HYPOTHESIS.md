# ORION procurement and inventory pilot hypothesis

Status: **offline design only**. Canonical source: `eabda90593f00ddc9b6ace6a4b9b9301fb3a01dd` (tree `1cfb49df43247ddc94ca9bed5f876cb3b52cbfe1`). No fresh customer authorization, customer metadata, or independent business outcome review was available for this evaluation.

## Capability inventory and limits

| Capability | Canonical implementation | Pilot implication |
| --- | --- | --- |
| Bounded ERPNext metadata and historical reads | `src/orion/discovery/erpnext_metadata_preflight.py`, `erpnext_historical_capture.py`, `erpnext_historical_sample.py`, `erpnext_study_router.py` | Possible only after a fresh exact-scope read-only grant and reviewed connector identity. Historical access is not inherited. |
| Authorization and isolation | `src/orion/learning/autonomous_loop.py` (`AuthorizationEnvelope`), `src/orion/pilot/broker_contract.py`, `broker.py`, `budgeted_transport.py` | Entity, field, request, tenant, and time limits must be bound before any customer request. |
| Evidence and provenance | `src/orion/history/evidence.py`, `src/orion/stores/sqlite_historical_evidence.py`, `src/orion/pilot/evidence_custody.py` | Retained batches are tenant scoped and sequenced; provenance and custody must be revalidated for each evaluation. |
| Investigation | `src/orion/learning/retained_quality_investigation.py` | Produces a descriptive missing-value **review candidate**. Applicability and business defect remain unknown. It ranks one candidate per call. |
| Prediction and outcomes | `src/orion/learning/prediction_ledger.py`, `event_outcome.py`, `organizational_cycle.py` | Ledger records predictions and resolved outcomes, but the current composed cycle targets sales. Procurement/inventory learning improvement is not established. |
| Available reviewed ERPNext metadata | No currently authorized customer metadata was opened in this work. Synthetic metadata exists in tests and the fixture. | Customer schema and report availability require owner review before scope issuance. |

## Preregistered hypotheses

1. **H1, incremental discovery:** on a later held-out window, ORION yields at least three independently validated procurement or inventory findings absent from the customer's existing ERP reports, and at least 20% more validated findings per analyst hour than the baseline. The same reviewer receives blinded, deduplicated candidate packets from both methods.
2. **H2, review quality:** after at least 30 adjudicated period-field candidates across both tracks, held-out precision is at least 0.80 and false-positive rate is at most 0.10. An unknown applicability label counts as requiring review, not as a verified defect.
3. **H3, net business value:** independently reconciled realized benefit exceeds measured analyst review cost and incremental infrastructure cost. Estimated exposure is reported separately and never counted as realized savings.
4. **H4, learning:** a preregistered procurement/inventory prediction target improves held-out Brier score or a preregistered discovery-quality measure across successive windows, compared with a frozen baseline, with no data leakage. Current canonical code has no qualified procurement/inventory outcome-learning cycle, so this hypothesis remains untested until that capability is reviewed.

## Evaluation design

- **Customer baseline:** owner identifies exact existing ERP reports, saved filters, refresh cadence, reviewer workflow, and issue ledger. Export the baseline results before ORION findings are shown. A generic missing-value report is used only for the synthetic offline exercise.
- **Ground truth:** two independent domain analysts review anonymized evidence packets without method labels. Resolve disagreements with a third reviewer. Record `valid_issue`, `expected_exception`, `insufficient_evidence`, or `duplicate`; only `valid_issue` is positive. Retain adjudication provenance and time spent. Synthetic fixture labels are test annotations, not customer adjudication.
- **Windows:** proposed customer calibration `2026-01-01` through `2026-06-30`; frozen held-out `2026-07-01` through `2026-09-30`. No rule, threshold, or candidate ranking may be tuned using held-out labels. The offline fixture uses January and February 2026 solely for deterministic testing.
- **Unit and metrics:** deduplicated `(tenant, company, entity, field, period, issue class)` candidate. Report TP, FP, FN, TN; precision `TP/(TP+FP)`; false-positive rate `FP/(FP+TN)`; recall; incremental validated discoveries `ORION positives absent from baseline`; validated findings per analyst hour; and uncertainty intervals. State denominators and missing labels. A field-level candidate is not an invoice-level defect.
- **Financial impact:** record independently verified realized reductions in paid loss, stock loss, or labor hours with source documents and counterfactual review. Report possible exposure separately; do not infer savings from missing fields alone.
- **Costs:** time each reviewer and baseline preparation; include connector operation, evidence storage, and adjudication costs. Compare total incremental cost against independently verified benefit.
- **Learning:** freeze the first-period method and predictions before later outcomes. Record every model revision in the ledger with prior resolved outcome references. No revision or improvement claim is made by the offline fixture.

## Stop conditions

Stop immediately for authorization expiry or revocation, scope mismatch, unexpected ERP permission, unsupported connector capability, clock rollback, missing/changed provenance, tenant leakage, transport budget breach, evidence custody failure, attempted ERP write, or execution authority appearing. Halt the business pilot if minimum adjudication is unavailable, held-out quality thresholds fail, incremental discovery is zero, or review cost exceeds verified benefit. No recommendation is executed automatically and no knowledge is promoted without separate approval.
