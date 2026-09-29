# Competitor harvest and the first measurable restaurant experiment

Inspection date: 2026-09-13. Canonical: `40c02b3a25a68b0ee7533fdf466bcb7d6f38f0a8`.
Scope: one read-only laboratory, existing architecture, prospective evidence.
This is an implementation plan, not a claim that an end-to-end restaurant pilot
or the broader constitutional vision is operational.

## Evidence standard

Official product descriptions establish advertised functionality, not internal
architecture, deployed reliability, model calibration, security certification,
or available third-party API rights. Except where explicitly noted, internal
schemas, model algorithms, training data, replay semantics, tenant enforcement,
and independently measured accuracy are **not publicly verified** here. A logo
does not establish an accessible connector. Do not copy proprietary software,
datasets, branding, or customer information; harvest public design principles.

## Competitor harvest matrix

A = implement the idea internally; B = integrate an existing product where the
operator already owns it and permits export; C = study, defer; D = do not build.
Each row covers architecture/data, onboarding/integrations, prediction/action,
and the material evidence gap. B is conditional, not a procurement commitment.

| Product and official source | Verified public description; evidence limitation | Harvest |
| --- | --- | --- |
| [Loop AI](https://loopai.com/) | Advertises AI coworkers and 100+ integrations reading revenue, cost, labor and customer data. Public integration count is not verified access to each API; internal lineage enforcement and forecast scoring remain unverified. | A: metric ownership, freshness, reconciliation and lineage contracts. B: existing customer's normalized exports if available. C: coworkers/multiple conversational surfaces. D: duplicate financial platform. |
| [Nory](https://www.nory.ai/agentic-ai) | Forecasting, scheduling and ordering assistants over connected operations. [Toast documents its integration](https://support.toasttab.com/en/article/Get-Started-with-the-Nory-Integration). Accuracy percentages are vendor claims with insufficient comparable calibration/denominator detail. | A: task-level measured outcomes. B: existing forecasts, schedules and inventory feeds. C: worker specialization. D: scheduling/payroll replacement. |
| [Marble](https://joinmarble.ai/) | Advertises POS/accounting/payroll integrations; onboarding agents mapping recipes, vendors and schedules; item demand forecasting; procurement/prep/scheduling with final human sign-off; document extraction and vision/voice counting. Automatic mapping is therefore not uniquely ORION's claim. Model design and independent onboarding accuracy unverified. | A: measure onboarding time and mapping quality. B: authorized structured exports. C: multimodal counts and compounding feedback. D: replacement inventory/labor system. |
| OpsAI | The official [opsai.com](https://opsai.com/) describes legacy IT discovery/cloud automation, not the restaurant platform described by a search directory. Restaurant product identity and event-driven execution claims remain unresolved. | A: event wakeups as an independently justified design already in #117. C: verify restaurant vendor identity. D: make no integration commitment. |
| [Restaurant365](https://www.restaurant365.com/ai) | Connects restaurant finance and operations; automates back-office tasks. [Official launch](https://www.restaurant365.com/in-the-news/restaurant365-introduces-r365-ai-the-only-intelligence-engine-built-on-the-full-restaurant-pl/) describes accounting/inventory/labor/POS context. Its site distinguishes some early-access features. No independently verified full-P&L reasoning accuracy. | A: reconcile operational claims to money and metric definitions. B: operator's existing accounting/inventory exports. D: rebuild accounting, payroll or ERP. |
| [Supy](https://supy.io/product-features/ai-predictive-ordering) | Forecast, stock, delivery date and coverage period determine proposed quantities; operator reviews/adjusts/submits. [Readiness article](https://supy.io/blog/learn-standing-orders-vs-predictive-ordering) explicitly requires history and recipe structure. | A: explain inputs and record overrides. B: existing ordering forecast as baseline. C: predictive ordering. D: procurement engine. |
| [Restrofi/RestroAI](https://restrofi.com/) | QR ordering, kitchen display, invoices and AI insights; a separate similarly named Restro AI app also exists. Treat the original identity as ambiguous rather than combining their capabilities. Internal models and public integration APIs unverified. | A: concise operator findings. B: existing operator exports only. C: multilingual insights. D: QR ordering, billing and POS. |
| [RAIOS](https://raios.pro/) | Connects operational context to explain/simulate/approve/track workflows. Site explicitly labels sample scenarios and says vendor compatibility must be confirmed; listed vendors are not verified native connectors. | A: detect/explain/quantify/route, clearly label evidence readiness. C: command center/digital twin. D: duplicate restaurant suite. |
| [OneTable](https://one-table.com/) | Inventory, labor, pricing and purchasing agents with guardrails; demo includes draft PO flow. 'SOC2 Ready'/'in progress' is not certification. Internal authorization and rollback enforcement unverified. | A: separate proposal, approval, attempt and verification. B: existing approved workflow later. C: execution adapter. D: rebuild vendor ERP. |
| [ElyteFlow](https://www.elyteflow.com/) | Advertises broad F&B suite and 21 agents. Breadth is product positioning, not proof of autonomous reliability or public APIs. | C: capability catalog reference. D: copy 192-feature/21-agent scope. |
| Holo | Searches found [Holo marketing](https://tryholo.ai/), not an identified restaurant transaction-propagation product. Prior attribution cannot be verified. | A: dependency invalidation as an independent ORION requirement. C: resolve identity. D: no vendor commitment. |
| [Zavo](https://zavopay.com/point-of-sale) | POS/order/table/payment surfaces and advertised voice AI; [company profile](https://www.ycombinator.com/companies/zavo) describes a digital workforce. Not proof of transferable agent internals. | C: job-oriented capability UX. B: existing POS export if permitted. D: payment processor/POS. |
| [Bagel](https://www.withbagel.com/platform) | Advertises Square/Toast/Lightspeed overlay joining POS, inventory, labor and suppliers; platform page includes early-access invitation. Root-cause explanations are not demonstrated causal identification. | A: link findings to evidence and financial consequences. B: authorized existing outputs. C: causal explanation. D: replacement operational suite. |
| [Per Diem](https://www.tryperdiem.com/) | Ordering and loyalty with Square; [Square marketplace](https://squareup.com/us/en/app-marketplace/app/per-diem) documents syncing menus, inventory, locations and loyalty. | B: operator's existing commercial signals. C: guest intelligence with consent. D: loyalty/mobile-ordering platform. |
| [Atlas Kitchen](https://atlas.kitchen/) | POS, ordering, logistics, loyalty and AI tools. [Google Cloud architecture case](https://cloud.google.com/blog/products/databases/how-atlas-scales-hundreds-of-cloud-sql-databases) documents Cloud SQL use; this does not establish its prediction model. | B: existing source data. C: tenancy deployment lessons. D: restaurant operating stack. |
| [Burnt](https://getburnt.ai/) | Food-distribution order/procurement/credit agents. Advertised accuracy/ROI are not independently validated here; API availability and legacy-system permissions need a scoped technical evaluation. | A: adapt above legacy systems. B: existing distributor outputs later. C: specialized execution. D: distribution ERP. |

## What the repository actually contains

Paths below refer to canonical unless identified as a PR. EXISTS means code
exists with relevant tests, not that a customer deployment was demonstrated.

| Capability | Status | Evidence and missing boundary |
| --- | --- | --- |
| Neutral evidence and observations | EXISTS | `contracts.py`, `ports.py`, kernel tenant boundary; evidence IDs/source/time. References are not inherently authenticated. |
| Environment discovery | PARTIALLY EXISTS | `discovery/planner.py`, metadata adapters, preflight, bounded study router; tests for metadata and authorized reads. Not zero-touch organizational discovery. |
| World model | PARTIALLY EXISTS | `understanding/graph.py`, `discovery/system_graph.py`: typed nodes/edges, provenance, tenant/status/validity. Broad process/people inference and durable temporal model incomplete. |
| Epistemic memory | PARTIALLY EXISTS | `understanding/hypotheses.py`, `validation/claims.py`, `knowledge/promotion.py`; historical SQLite evidence/checkpoints. Constitutional memory/firewall/forgetting ambitions exceed these implementations. |
| Unknown detection | PARTIALLY EXISTS | `learning/autonomous_loop.py` coverage/gaps and opportunity ranking; not a validated catalog of all organizational unknowns. |
| Prediction measurement | PARTIALLY EXISTS | `learning/shadow_backtest.py` evaluates currency and due intervals chronologically. This PR adds prospective binary prediction/outcome persistence; no complete general forecast/calibration/economic ledger yet. |
| Outcome learning | PARTIALLY EXISTS | comparison, backtest, retained-quality investigation and reassessment code; no demonstrated prospective operator-override learning. |
| Capability evolution | PARTIALLY EXISTS | `StudyCapability`, `AuthorizationEnvelope`, reauthorization and promotion documents. Classification is not an earned-authority state machine. |
| Cross-environment learning | MISSING | `KnowledgeStore` explicitly refuses common promotion/retrieval pending generalization. Keep that boundary. |
| Physical/digital sensor abstraction | PARTIALLY EXISTS | `DiscoveryAdapter -> Observation -> Evidence` is reusable; camera/IoT event quality, clocks and calibration absent. |
| Event worker | PARTIALLY EXISTS | #117 draft, outside canonical: queue, routing and finite injected-handler loop. Handler effects are at-least-once; durable effect idempotency remains handler responsibility. |
| Activity watcher | PARTIALLY EXISTS | #116 draft, outside canonical: sanitized optional sink and offline demo; not a production monitoring service. |
| Review automation | PARTIALLY EXISTS | #114 draft; ordinary CI passed previously, Claude secret preflight failed. Integration/review has not completed. |
| Live restaurant connector | MISSING as demonstrated end-to-end capability | ERPNext reader/launcher code exists; no authorized customer connection or sustained live proof established by this inspection. |
| POS/payroll/payments/accounting replacement | NOT NEEDED | Existing source systems retain transactional ownership. |

Review follow-ups on #117: queue dedupe is not exactly-once handler effects;
worker report's hardcoded external-read/write zeros cannot attest to arbitrary
handler behavior; attempt budget is checked after handler failure and needs a
pre-dispatch check after stale-lease recovery; worker depends directly on SQLite.
Treat these as review blockers before wiring customer evidence to that worker.

## Concrete differentiation spec: extend owners, do not replace them

1. **Discovery:** add an export adapter implementing `DiscoveryAdapter` and a
   `SourceManifest(tenant, source, captured_at, available_at, checksum, schema,
   units, timezone, row_count, allowed_fields)`. Reuse `Observation`, metadata
   understanding, planner and scope checks. Mapping output records candidate,
   evidence IDs, confidence and reviewer disposition. Test renamed fields,
   unknown tables, denied fields, ambiguous units and incomplete exports.
2. **World model:** extend existing graph projection with stable source keys,
   `valid_from/valid_until`, source-available time and superseding evidence IDs.
   Project inventory/recipe/supplier dependencies; inferred edges remain
   hypotheses until verified. Test tenant isolation, contradictory joins and
   bitemporal replay. Do not equate graph linkage with causation.
3. **Unknown detection:** reuse coverage/opportunity ranking; add
   `Unknown(target, missing_evidence, impact, acquisition_cost, reason)` from
   unmapped fields, stale observations and competing hypotheses. Expose
   `rank_unknowns(objective, graph, coverage)` returning existing study intents.
   Test abstention and rank changes after new evidence; never widen the envelope.
4. **Prediction ledger (this increment):** `Prediction` records tenant/key,
   target-definition version, model version, issue/cutoff/horizon, probability
   and evidence UUIDs. `PredictionLedger.record` commits before horizon;
   `resolve(Outcome)` appends observed boolean and provenance; `score(tenant)`
   excludes missing outcomes and computes Brier, fixed-threshold confusion
   counts and lead time from actual insertion time. Exact replay is idempotent;
   conflicting replay fails. There are no action methods. Database and caller
   clock are trusted. Caller must verify evidence membership and availability;
   references alone do not prove provenance. Brier is a proper score, not a
   complete calibration analysis. Use separate cohorts per target/model.
5. **Outcome learning:** proposed `OutcomeResolved` and `OverrideRecorded`
   events reference ledger key, evidence IDs, actor scope and reason. Overrides
   are opinions/interventions, not outcome truth. Retrain/re-rank only after
   chronological holdout evaluation; retain prior model, compare baseline and
   candidate on the same target set. Test late labels, corrections and feedback
   leakage. This wiring is next, not implemented by the ledger.
6. **Capability evolution:** proposed `CapabilityEvidence(capability, scope,
   test_version, successes, failures, exposure, review_id)`. Keep reliability
   status orthogonal to authorization grant. `evaluate_readiness` returns an
   assessment, never an envelope. Tests: evidence cannot mint authority,
   revocation takes effect before invocation, failed verification stays failed.
7. **Cross-environment learning:** deferred `GeneralizationCandidate` with
   abstract schema, consent/purpose, leakage review, source class and validation
   results. Customer payloads/IDs never enter common knowledge. Test linkage
   leakage and rejection before any promotion; existing refusal remains.
8. **Sensors:** adapters emit existing `EvidenceKind` plus source manifest;
   modality, event time, availability time, calibration/version and uncertainty
   are adapter metadata. `SensorHealthChanged` invalidates freshness assumptions.
   No cameras, employee recognition, GPS or raw audio in the first pilot.

Events above are proposed integration contracts; they do not imply #117 is
merged or these handlers exist. No constitution rewrite or framework migration.

## First real restaurant experiment: preregistered read-only protocol

**Smallest credible MVP:** one site, one POS/ERP export route, existing discovery
and graph, one unknown queue, two binary findings/predictions, this ledger, and
an aggregate review packet. No dashboard or autonomous execution.

**Input envelope:** operator-authorized encrypted local exports; exact tenant,
company, date range, allowed fields and expiry. Start with 8-12 weeks if
available, but report insufficient history rather than fabricate it. Source
scope includes sales/refunds/discounts, purchase receipts, invoices, inventory
counts/movements, recipes/BOM and unit conversions, suppliers, expenses,
workforce transaction timestamps and pseudonymous shift IDs. External signals
are optional and only included with known publication times. Do not ingest
employee names, payroll bank details, payment-card data or customer contacts.
No production data in this public repository or ChatGPT research artifacts.

**Data acceptance:** hash and inventory files; retain source-owned IDs locally;
validate row counts, units/currency/timezone, duplicate keys, voids/returns and
freshness. Reconcile daily sales to source totals with a documented rounding
tolerance; quarantine unexplained discrepancies. Distinguish denied access,
not available, not observed, and nonexistent entities.

**Discovery test:** withhold operator's entity/relationship answer key from the
inference process. Give source metadata and bounded samples, not the answer
mapping. Independently review inferred entities/edges and a sample of unknowns.
Record precision, recall against the answer key, elapsed onboarding time,
operator minutes, unknown validity and unresolved ambiguity. Human mapping
must be counted; do not claim zero-touch discovery after manual modeling.

**Prediction targets:** select only where labels are independently observable:
(a) stockout within next 24 hours for selected ingredients, requiring verified
counts/receipts/recipe conversion; (b) invoice/receipt mismatch at next daily
reconciliation, with exact amount/unit tolerance. If either lacks trustworthy
labels, abstain and report that unknown. These are target definitions, not new
forecast engines. Compare historical base-rate/seasonal baselines and any
existing operator/vendor model. Freeze each prediction before its horizon.

**Evaluation:** chronological rolling-origin historical rehearsal, then 14 days
prospective shadow operation. Track every eligible target and abstention,
not only issued predictions. Report Brier, reliability bins with counts,
precision/recall, FP/FN, alert volume, lead time, operator overrides and minutes,
cost per useful finding, unresolved labels and source freshness. Separate
model versions and targets; report confidence intervals with sample counts.
With few positive events, call performance inconclusive. No accuracy percentage
without denominator or baseline. Never score future-unavailable evidence.

**Proposed go/no-go gates:** 100% issued predictions trace to authorized evidence;
zero cross-tenant disclosures/writes; zero accepted post-horizon insertions;
at least 90% precision on an independently reviewed mapping sample, with sample
size and recall disclosed; source totals reconcile or affected targets abstain.
Advance only when prospective loss beats the preregistered baseline on matched
targets and uncertainty is acceptable to the operator. A 30-day pilot may
establish feasibility without establishing statistical superiority.

**Outcome learning proof:** hold frozen model A against candidate B after one
reviewed correction; both predict the same later holdout. Record degradation
as faithfully as improvement. Overrides do not replace actual outcomes.
Economic exposure may be estimated separately; realized savings require a
later intervention/control comparison. Initial `economic_value` stays null.

**Live activation is not performed:** no restaurant credentials, tenant scope,
data package or external-access ledger was supplied. This PR supplies the
measurement primitive and experiment contract, not a functioning live pilot.

## Ready-made components and selection gates

| Need | Reuse/integrate | Why / boundary |
| --- | --- | --- |
| ERP | Existing ERPNext adapter plus [Frappe REST](https://docs.frappe.io/framework/user/en/api/rest) | Reuse local authorization; dedicated read-only user. Confirm server permissions, pagination and rate limits before live activation. |
| POS | [Square permissions/API](https://developer.squareup.com/docs/oauth-api/square-permissions), [Toast developer guide](https://doc.toasttab.com/) | Use operator's actual source. Square needs relevant read scopes only; Toast access/partner eligibility must be confirmed. Prefer exports to waiting on a partnership. |
| Connector breadth | [Airbyte source catalog](https://docs.airbyte.com/integrations/sources) | Later only when a supported source reduces measured adapter work. Do not assume catalog entries provide every field or CDC. |
| Documents/OCR | [Docling](https://docling-project.github.io/docling/), optionally [Textract AnalyzeExpense](https://docs.aws.amazon.com/textract/latest/APIReference/API_AnalyzeExpense.html) | Local conversion first; cloud extraction only with data-processing approval. Validate line items and totals against source, retain extraction confidence. |
| Forecast baselines | [StatsForecast](https://nixtlaverse.nixtla.io/statsforecast/docs/getting-started/getting_started_complete.html) | Proven baseline/cross-validation machinery behind a predictor adapter; no new forecasting platform. |
| Anomaly detection | Existing deterministic comparisons, robust residual thresholds | Begin with auditable rules and false-positive measurement; no separate anomaly service yet. |
| Event transport | #117 SQLite queue | Review replay/attempt semantics before integration; Kafka unnecessary at one site. |
| Observability | #116 sanitized sink; [OpenTelemetry](https://opentelemetry.io/docs/) later | Export aggregate safe events only; do not auto-instrument raw customer payloads. |
| Graph/vector database | Existing graph and SQLite | Durable graph adapter later if measured query/storage limits justify it; no vector database required for first proof. |
| Knowledge extraction | Existing metadata/hypothesis pipeline; local document parser | LLM suggestions are candidate mappings; validate against source and operator answer key. |
| Vision | None now | Inventory counting quality and consent would require a separate experiment. |
| Agent runtime/workflows | Existing bounded loop and authorization | No additional agent framework. External workflow execution deferred; approval must remain an ORION boundary. |

## Account-specific plugins

Directory inspected on this account: GitHub installed and successfully used;
Google Drive available but not installed. Targeted searches for ERPNext/Toast/
Square and Cloudflare returned no matching plugins in this directory response.
This does not prove the providers lack APIs or that another account has the
same catalog. Native web research and local coding already work.

- GitHub: now; saves repository/CI/PR handling. Repository content read/write,
  issues/PR write and Actions read are sufficient for this work; workflow write
  only when editing automation. Do not expand installation to unrelated repos.
- Google Drive: only if operator exports are there; saves manual file transfer.
  Seek read-only access to a dedicated export collection, check actual OAuth
  consent scope (ChatGPT behavior permissions do not restrict OAuth scopes).
  No installation initiated without a known source need.
- Cloud/deployment/database: later, no connection for this local pilot.
  Use scoped project/service credentials when deployment is actually selected.
- Communications: later; no mailbox/chat access merely for summaries. Operator
  feedback can be a local review file. Sending messages requires separate scope.
- Claude review: existing workflow setup needs a repository secret provisioned
  by maintainer, not credentials pasted into chat. Account plugins do not supply
  a GitHub Actions secret. Do not weaken the preflight to make CI appear green.

## Thirty-day execution plan and cuts

| Priority / days | Deliverable | Exit condition |
| --- | --- | --- |
| P0 / 1-3 | Review current PRs, this ledger, exact pilot access/export contract; resolve #117 review findings | Maintainer-approved code; named source scope; no unresolved identity/time/replay defects |
| P0 / 4-7 | Export adapter and reconciliation fixture, verified evidence links | Source totals reconcile; unknowns/denied fields explicit; no live writes |
| P0 / 8-10 | Blind environment discovery evaluation | Mapping/relationship precision and recall, operator time and unknown queue reported |
| P0 / 11-14 | Freeze target definitions, baselines, held-out replay and alert budget | Every prediction has source cutoff, timestamp and independent label protocol |
| P1 / 15-28 | Prospective shadow collection, daily review, one controlled model correction | All outcomes/abstentions retained; calibration/counts/lead time and operator effort reported |
| P1 / 29-30 | Matched baseline report, continue/change/stop decision | Explicit evidence of benefit or explicit inconclusive/failure result |
| P2 | Second unfamiliar environment, reusable abstract mappings, stronger calibration, modality adapters | First site earns expansion; no private cross-tenant transfer |
| CUT | POS, payments, accounting, payroll, loyalty, giant UI, restaurant agent catalog, generic forecast engine, camera rollout, Kafka, autonomous writes | No implementation budget in this pilot |

The immediate critical path is authorized evidence -> reconcile -> discover ->
commit prospective predictions -> independent outcomes -> measure and revise.
Adding integrations or agents without shortening that path is out of scope.

## Offline prediction measurement demonstration

Run from an installed checkout of this branch:

```bash
python -m orion.learning.prediction_ledger
```

No API key, external service, customer data or new dependency is required.
The command creates a temporary SQLite database, records five synthetic binary
predictions, reopens the database with a clock advanced by one day, and resolves
four outcomes. It reports Brier loss `0.34`, one of each confusion-matrix outcome,
one pending label, and `execution_allowed=false`. The pending label does not
contribute to the score. Economic value remains `null`.

The database is removed when the demonstration ends. Persistence is exercised
across ledger instances, not by leaving a customer store behind. The fixed clock,
invented probabilities and fixture evidence references make this a repeatable
measurement demonstration: it proves neither forecasting skill nor learning
improvement nor a live restaurant connection. Existing tests separately verify
rejection of late predictions, future outcomes, replay conflicts and wrong-tenant
outcomes. This command is added on the draft branch; it is not yet on the canonical
laboratory branch until the governed review and merge are complete.
