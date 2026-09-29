# Research-to-engineering decision: minimum read-only pilot

## Verified starting point

Canonical remains 40c02b3. PR #119 at 74227f2 was open/unmerged at inspection;
this follow-up fixes cohort selection under issue #122. PR #117 has durable
bounded event delivery (aa09aa1); PR #121 (200834c) composes those modules with
a trusted offline receipt handler. Neither dependency is canonical yet.

Reuse: contracts.Evidence/Observation, ports.DiscoveryAdapter/EvidenceStore,
discovery.erpnext_adapter (bounded HTTPS GET, redirect rejection), metadata
and governed record readers, discovery.pipeline, understanding.graph,
learning.retained_quality_investigation and investigation_disposition,
learning.shadow_backtest, prediction_ledger, events queue/worker and
learning.event_outcome. Do not build competing evidence/graph/ledger stores.

PR #119 correctly records prospective issuance/cutoff/horizon, tenant identity,
immutable outcomes and replay conflicts, Brier/confusion counts, pending-label
exclusion and insertion-based lead time. UUID references do not authenticate
source membership. A Brier score is not a complete calibration analysis.
PR #121's injected lookup is a trust seam, not a deployed authenticated adapter.

Missing: admitted real data, general semantic mapping evaluation, explicit
important unknowns linked to decisions, comparable prospective experiment arms,
feedback-driven belief revision with measured improvement. Fixture success does
not establish any of these. No experimentally established competitive lead.

## BORROW → INTEGRATE → BUILD → CUT

| Decision | Capability / reference | Engineering decision |
|---|---|---|
| BORROW | Statistical prediction: StatsForecast; anomaly/online methods: River | Use frozen simple baselines first. Add a dependency only after a selected target needs it; never build a generic forecasting library. |
| BORROW | OCR/document extraction: Docling or existing customer service | Only if required evidence is trapped in invoices. Keep original evidence and extraction uncertainty; parsing is not verified fact. |
| INTEGRATE | Existing ERP/POS exports and APIs; Supy if already installed | One bounded input adapter for the actual restaurant, not a universal connector program. Use existing ERPNext reader when applicable. |
| INTEGRATE | Process mining: existing Celonis outputs / PM4Py | Only if object/event mapping becomes the measured bottleneck. Check PM4Py licensing before embedding. |
| INTEGRATE | Graphiti temporal extraction, existing semantic models | Optional replaceable projection when current graph cannot answer pilot queries. Preserve authoritative evidence/validity in ORION; no migration now. |
| BUILD | Evidence admission, availability-time enforcement and claim dependencies | Extend existing provenance contracts; never trust source IDs or metadata alone. |
| BUILD | Uncertainty acquisition and typed belief revision | Choose questions by decision value/cost, retain competing hypotheses, invalidate dependents after corrections. |
| BUILD | Controlled comparative experiment and outcome association | Reuse #119/#121; record matched opportunities, missing labels, interventions and cohort versions. |
| CUT | POS, ERP, payments, payroll, loyalty, giant UI, fixed restaurant-agent catalog | Incumbents already supply them; they do not test the learning thesis. |
| CUT now | New graph DB/runtime/streaming stack; cameras, robotics, causal digital twin, cross-tenant transfer, autonomous execution | Adds cost and approval surface before evidence of benefit. Retain as conditional research, not pilot requirements. |

Evidence carried forward from the completed research, with targeted documentation
checks rather than another landscape survey:
[StatsForecast](https://nixtlaverse.nixtla.io/statsforecast),
[Graphiti](https://github.com/getzep/graphiti),
[Palantir Ontology](https://www.palantir.com/docs/foundry/ontology/overview),
[Aera Decision Data Model](https://www.aeratechnology.com/decision-data-model/).
Palantir documents an operational ontology; Graphiti documents temporal graphs,
provenance and learned ontologies. Aera advertises context/decision/outcome memory;
its learning benefit is a vendor claim, not independent proof. These mechanisms
are not novel ORION inventions. Restaurant vendors have stronger integration and
operational product breadth; prior research did not establish comparable learning
accuracy under equal inputs. Do not equate feature breadth with measured accuracy.

Potential differentiation: lower operator effort and better decision outcomes
from discovering and correcting important unknowns under equal evidence/compute
budgets. This is a hypothesis requiring the experiment, not a current moat.

## Smallest genuine experiment

One unfamiliar organization, one admitted source (authorized export is sufficient
for the first observational run), source metadata and timestamped operational
records. Do not hand-model the business; provide data scope, source dictionary
and independent human evaluation labels only. Keep domain labels at the adapter
edge. Infer candidate entities/relations from source structure and observed keys;
mark inferred links as hypotheses until validated.

Offline chronological replay is a rehearsal. A real prospective period requires
predictions inserted before their horizon and subsequent newly available outcomes.
Use historical data only for discovery/training, then a frozen prospective target
contract and two or three matched arms: baseline retrieval + simple predictor;
persistent verified memory control; ORION uncertainty selection and typed revision.
All arms share model/tools, evidence availability, opportunity IDs and budgets.
Compare against the memory control to avoid claiming a win from merely having memory.

Targets must be generated from generic supported hypotheses and independently
validated for operational relevance before registration, not hard-coded stockout
rules. Abstain where no labelable, useful target can be established. Version any
change in target semantics; never retrofit an old forecast's label.

Measure entity/relationship precision and recall against a withheld human map,
useful unknown precision, operator minutes, matched Brier/confusion/coverage,
lead time, overrides and post-correction performance. Report missing labels and
interventions separately. Zero customer writes is a hard invariant. A two-week
prospective window is an initial checkpoint, not guaranteed statistical proof.
No differentiation claim unless improvement over the strong control survives
matched coverage and cost accounting.

## Sequential Codex contracts

All paths below are relative to repository root. New paths are proposals unless
marked implemented. Each task needs a scoped issue and feature branch. Codex can
implement offline code/tests autonomously; Claude reviews exact commits before
maintainer merge. No task authorizes credentials, customer reads or writes.

### T0 — Valid cohort scoring (implemented in this follow-up)

Objective: stop silently pooling targets/models in PR #119.
Files: src/orion/learning/prediction_ledger.py; tests/test_prediction_ledger.py.
Interface: score(tenant_id, *, target_definition=None, model_version=None).
Both filters or neither; an implicit multi-cohort request raises ValueError.
No schema migration. Empty selected cohorts stay empty; pending labels remain
excluded. Tests cover ambiguous targets/models, exact selection, empty/wrong
tenant, incomplete filters and all prior behavior. Acceptance: passing gates,
no pooled ambiguous score. Autonomous: yes. Claude: yes, measurement integrity.
Dependencies: #119; propagate into #121 after review to avoid stale composition.

### T1 — Admit one existing operational data source

Objective: bind an owner-authorized dataset to the existing discovery/evidence
contracts without a new evidence system. First inspect the actual source and
reuse ERPNext's governed metadata/record path if applicable. Otherwise implement
src/orion/discovery/export_adapter.py plus tests/test_export_adapter.py; compose
through ports.py and existing pipeline, avoiding changes to authorization policy.
Schema: ExportManifest(version, tenant, source, dataset_digest, acquired_at,
available_at, tables[name, path, digest, row_bound, fields], authorization_ref).
Authorization reference must resolve through the existing verified envelope;
a supplied reference/string/digest is not a grant. Adapter accepts admitted
bytes or bounded files and returns existing read-only Observations with digest,
row identity and source timestamps as provenance. Existing envelopes must support
the chosen scope; otherwise explicitly scope a policy extension for separate review.
Tests: digest mismatch, unavailable/future data, tenant mismatch, missing grant,
path traversal/symlink/oversize rejection, replay identities, input unchanged,
no sockets and no partial evidence append on failed validation.
Acceptance: one private authorized dataset replays into existing graph/evidence
without credentials in code or logs. Autonomous: implementation yes; real run
blocked on supplied authorized data and compatible envelope. Claude: required.

### T2 — Evidence-backed discovery and unknown report

Objective: discover structure and prioritize unresolved interpretations without
restaurant literals. Extend understanding/metadata.py, understanding/graph.py,
learning/retained_quality_investigation.py only where their seams suffice; add
learning/environment_questions.py and tests/test_environment_questions.py.
Schema: MappingHypothesis(id, tenant, entity/relationship, evidence_ids,
valid_at, available_at, alternatives, status); Unknown(id, hypothesis_ids,
missing_evidence, decision_impact, acquisition_cost, priority_reason).
Use existing inference/provenance semantics, not another canonical graph.
Tests: same behavior on renamed non-restaurant schemas, competing joins, missing
keys, isolated tenants, stale evidence, unknown business semantics and abstention.
Acceptance: withheld-map precision/recall and unanswered questions are measurable;
no relationship promoted solely because names match. Autonomous: fixture code
yes; meaningful validation needs T1 + human reference map. Claude: required.

### T3 — Matched prospective experiment runner

Objective: compare learning behavior on the same opportunities, not different
prediction sets. Add learning/pilot_trial.py and tests/test_pilot_trial.py; reuse
prediction_ledger, event_outcome and shadow_backtest. Do not rebuild them.
Schema: TrialSpec(tenant, target_definition, arm_versions, cutoff, horizon,
label_rule_version, budget); Opportunity(id, due_at, evidence_ids); ArmDecision
(opportunity_id, prediction_id OR abstention_reason); OutcomeReceipt from #121.
Borrow a baseline only when a target needs it. Target semantics and outcome
binding must be verified independently of a model's own prediction.
Tests: outcome leakage, missing-arm/label handling, exact opportunity pairing,
cohort selection, prospective insertion, replay, late labels and unequal budgets.
Acceptance: reproducible per-arm and paired coverage/loss report; no causal value
or learning gain claimed from aggregate Brier alone. Autonomous: code yes;
prospective measurement blocked on T1/T2 and elapsed real horizons. Claude: required.

### T4 — Typed correction and evidence invalidation

Objective: learn from corrections without treating preferences as ground truth.
Extend learning/investigation_disposition.py and existing epistemic invalidation
owner after locating it; add learning/pilot_feedback.py and matching tests.
Schema: Feedback(id, tenant, kind=correction|preference|policy|intervention,
claim_id, evidence_ids, actor_ref, available_at); Revision(prior_id, new_id,
reason, evidence_ids). Bind actor scope through existing authority controls.
Tests: preference cannot resolve Outcome; intervention stratifies evaluation;
correction invalidates dependent hypotheses; unchanged evidence does not boost
confidence; replay/tenant isolation; historical predictions never rewritten.
Acceptance: a blinded correction changes only affected future reasoning and its
benefit is measured on later matched opportunities. Autonomous: code yes;
validated feedback and T2/T3 required for real result. Claude: required.

### T5 — Run the pilot and adjudicate the thesis

Objective: execute T1-T4 under a fixed read-only protocol. Extend the trial report,
not product UI. Report signed/verified input identities, versions, opportunities,
coverage, scores, lead time, operator effort, failure/abstention and interventions.
Tests: replay reproduces reports; fail closed on changed scope/data/versions;
source remains unchanged; no retrospective replacement of missed predictions.
Acceptance: compare ORION against persistent verified memory control at equal
budget. Report inconclusive if too few outcomes or insufficient improvement.
Autonomous: offline orchestration only; real acquisition requires the separate
access ledger and human truth labels. Claude: protocol + exact-code review.
Dependencies: reviewed code, admitted data, elapsed horizons, independent labels.

## Immediate non-code blockers

No authorized restaurant dataset or new live-access ledger was supplied here.
Independent review and maintainer merge remain required by AGENTS.md. Automatic
Claude review configuration was previously blocked; never assume it ran because
CI passed. These are release/data prerequisites, not reasons to buy API credits
or manufacture more synthetic claims of intelligence.
