# Read-only pilot review hardening

Issue #135 follows the tested semantic gate. The verdict remains NOT-READY for
external pilot activation. A passed offline semantic gate is not an isolated live
runtime. No authorization, transport or deployment gate was weakened.

## Trace and concrete corrections

The legacy demo fetched the source twice: knowledge referenced the first batch,
while the kernel stored a newly acquired second batch with different evidence
IDs. It now feeds one immutable observation tuple to storage and understanding.
The regression test compares every promoted knowledge reference with stored IDs.

Legacy hypothesis generation and graph projection accepted only dictionaries,
so immutable admitted record mappings were ignored or projected as outer payloads.
Both now accept the canonical Mapping contract and preserve the actual record and
its evidence identity. This adds no schema-name or business-role mapping.

The count-based prototype validator accepted an empty hypothesis at default LOW
assurance. Empty/default support now remains unvalidated; malformed counts,
counts exceeding unique references and duplicate references fail closed.
Contradicted/invalidated hypotheses cannot be revived by supplying a count.
Knowledge promotion also requires an explicit tenant and unique UUID provenance.
The demo explicitly requests one-witness validation for a directly observed
structural object. This is not commercial semantic validation. Caller-supplied
counts and directly constructed ValidationDecision objects remain unsuitable for
a deployment trust boundary; that gate is still blocked.

`shadow.semantic_review` composes the existing semantic study/checkpoint and
ShadowDecision contracts. A review is produced when a previously validated role becomes contradicted
or independently grounded supporting and contradicting sources disagree. Eliminating an unsupported alternative is normal
learning and does not trigger a false-positive recommendation. UNKNOWN alone
produces no invented risk. The result contains exact scope, evidence/grant
references, checkpoint hash, deterministic audit/decision IDs and an explicit
`not_attempted` execution status. It creates no source observations, knowledge
entries, transactions, grants or execution capability. This is an auditable value
object, not a protected durable audit collector.

## Executable restaurant scenario

`test_restaurant_settlement_discrepancy_through_actual_governed_pipeline` models
synthetic restaurant settlement records with independently captured reconciliation
components. Its business schema has opaque identifiers; business labels exist only
in test commentary, not field mappings supplied to the engine.

It invokes the actual metadata launcher/ERP normalization and separate pilot
record admission, then existing semantic evaluation and world-model revision.
Initial evidence supports a monetary measure. Explicit independent corrections
contradict that interpretation and identify a different candidate. The resulting
review decision identifies the evidence and rationale while execution remains
false and status remains not_attempted. Replaying analysis neither collects more
records nor changes the source. Reference-only recovery reproduces the earlier
review state. No successful action or simulated financial outcome is invented.

This is a complete **observation-to-disabled-review** path, not the requested
full live agent/approval/action service. That service is absent, and the test does
not pretend otherwise. No live integration is exercised.

## Repository and architecture assessment

The updated source inventory covers all production/tool Python modules. The
34-requirement gap matrix records implementation evidence and unresolved pilot
constraints. Entrypoints are local demos, legacy discovery/study CLIs, offline
proposal composition, semantic verification and the denying readiness CLI.
SQLite stores implement legacy historical evidence and study checkpoints, not a
production semantic admission archive or protected authorization service.
There is no reviewed deployed supervisor, authenticated control plane, isolated
agent worker, secret mount or production health/monitoring service in this tree.
Dependencies remain standard-library runtime plus pytest/Ruff for development.

The canonical observation/graph contracts, customer-scoped knowledge, scoped
pilot reads, epistemic UNKNOWN/contradiction and shadow-only recommendation support
the approved architectural direction. The Constitution's full separated memory
systems, controlled forgetting, TRRE 2.0, model/sensor/communication/action runtime,
zero-trust containment and recursive promotion controls are not all executable
integrated services. Legacy field-specific historical analysis must not be
represented as generic discovery. No such subsystem was invented to fill a box.

## Blocking deployment boundary

Both live and pilot activation remain blocked by:

- trusted same-process callable/transport construction outside the governed path;
- credentials sharing the application process and no independently isolated egress broker;
- no authenticated grant issuer, source-instrument registry or fully verified technical read bindings;
- no production admission archive, independently retained checkpoint index or protected lifecycle audit;
- incomplete tenant-bound deployment storage, retention/sensitivity policy, worker supervision and monitoring;
- no fully exercised second-protocol live lifecycle or complete prediction/approval/action deployment.

The local bubblewrap isolation probe failed; Docker is unavailable. This
execution environment cannot certify an OS-enforced live containment profile.
Writing an unexercised deployment manifest would not close that gate. Existing
startup remains denied. Production-scale forecasting, additional semantic roles
and execution are not added to the current scope.

The next deployment increment is a separately supervised read-only broker with
exclusive credential/grant/audit custody and mandatory egress limits. It must be
verified in an isolation-capable local test environment before any pilot source
configuration or activation. This does not require the operator to supply ERP
DocTypes, fields or business mappings.

## Issue #135 completion: audit and recovery contract

Review artifacts now retain all bounded evaluated inputs, evidence classes,
original collector roots, current claim states and evaluator version even when
the result is UNKNOWN or supported and no proposal is produced. Original validated
support remains referenced after explicit corrections supersede it. The immutable
result exposes execution_allowed=false and execution_status=not_attempted. It
contains no record values and is still an audit value object, not a durable log
service. Lineage authenticity still depends on the reviewed archive/instruments.

Semantic checkpoint format 2 binds semantic-rules-v1 explicitly, alongside
existing policy and evidence fingerprints. Format 1 and different evaluator
versions reject; there is no silent migration. Old laboratory checkpoints must
be reconstructed from the original scoped archive under reviewed code. Future
semantic algorithm changes must change the evaluator version. The existing
base role/process checkpoint formats are unchanged.

The instrumented local restaurant fixture now optionally routes metadata and
record reads through the actual #131 permit/budget transport and AttemptJournal.
The test verifies attempt accounting, journal restoration, durable stop denial,
and metadata budget exhaustion before further collection. No new production
transport, credential or network implementation is introduced. Test integrity
keys are generated locally and never committed or returned.

Fresh-interpreter A/B/C recovery now compares review audit identity and proposal
state in addition to semantic claims/revisions, and still rejects revoked
continuation before adapter invocation. Separate tests reject using a review
artifact as a request or grant. The offline proof does not change release gates.
