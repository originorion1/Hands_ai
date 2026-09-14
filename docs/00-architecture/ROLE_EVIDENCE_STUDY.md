# Scoped role evidence study

`understanding.role_study.RoleStudy` consumes archived pilot metadata and record
observations. It composes the existing `Hypothesis`, `GraphNode`, `GraphStore`,
`FieldDeclaration` and pilot admission contracts. It has no reader, transport,
credentials, grant issuer or execution method.

## What is identifiable

Opaque declarations generate competing, falsifiable **observable** roles:
nonnegative/nonpositive numeric values, two possible date orderings, and
repeated/distinct reference values. Names never select a role. These are a small
explicit predicate vocabulary, not a restaurant mapping or a learned business
ontology. Resource business roles and reference targets remain unidentified.

Two distinct source records supporting a predicate permit sample-scoped
validation. One counterexample invalidates the universal sample predicate even
when its supporting fraction is high. Equal dates, invalid types, and missing
values provide no discriminatory evidence. Scores are descriptive sample support
fractions, not calibrated probabilities. Reacquiring one record cannot supply a
second independent witness. Evidence changes recompute conclusions; earlier
support and contradictory evidence remain traceable.

A positive number could be money, mass, a balance or a counter. A repeated
reference could denote a supplier, an owner or a category. Neither structural
regularity nor cardinality distinguishes those meanings. The business answer is
therefore **unknown**, not validated by this experiment. Date ordering likewise
cannot identify an event date, accounting date or causal direction. Semantic
validation needs independently evidenced process context; no such context is
fabricated here. This increment proves scoped belief revision, not autonomous
business understanding or all of the organizational-learning thesis.

## Authenticity and isolation

The injected trusted archive resolves an evidence UUID to the exact immutable
launcher-admitted `Observation`. For records it also resolves `('scope', UUID)`
to the original `PilotRequest`. Both entries must be populated by trusted
composition after successful admission, never from untrusted caller assertions.
No production archive wiring is supplied. This is not cryptographic attestation
or protection against hostile same-process code or a compromised archive.

The study verifies archive membership, tenant/company/source/resource, exact
field scope and reviewed date window before atomically retaining a batch. It
limits each batch to 25 and each study to 100 observations. Acquisition expiry,
revocation, evidence kind/source and row binding remain owned by the existing
pilot launcher. Expiry of a grant does not retrospectively erase admitted
historical evidence or authorize another read. Removing evidence from the
archive prevents further conclusions using it.

Provenance references resolve to full metadata or record observations, including
source, grant ID/digest, acquisition time and upstream identities. Claims retain
schema evidence plus separate supporting and contradicting IDs. Request objects
and caller-constructed provenance cannot substitute for trusted archive entries.

## Minimum-observation proposal

The planner groups competing predicates by resource and fields. It ranks unknown
or contradictory groups by unresolved alternatives per requested field-cell,
with double priority for contradictions. This deterministic heuristic is not a
claim of optimal information gain. Already sampled windows are skipped.

The caller supplies a bounded investigation window (at most 31 days), budget,
row ceiling (2–25), and trusted field sensitivity classifications. Missing or
non-public classifications exclude a field. No operator-supplied schema or
business mapping is required. Output is one resource, exact analytical fields,
time bounds, record ceiling, hypothesis IDs, reason and estimated analytical
cell cost. It is a **proposal**, not a `PilotRequest` or a grant.

Identity, company and date-filter bindings remain explicit blockers until a
separate control plane establishes them. Required technical audit fields add to
the analytical estimate and require their own scope/sensitivity review. The
planner cannot silently choose a first date field or manufacture these bindings.
A metadata grant cannot execute the proposal. The offline fixture separately
approves a record grant with opaque technical bindings, then calls the existing
`launch_pilot_read`; those fixture bindings are not discovered business roles.

## World model and experiment

`world_model()` builds a fresh tenant-bound current graph snapshot. Only validated
or contradicted sample claims enter it, with provenance and `sample_only=true`;
unknown interpretations remain hypotheses. Older snapshots are historical, not
mutated or silently reused as current knowledge. Durable revision storage and
cross-study reconciliation are outside this increment.

Run `pytest -q tests/test_role_study.py tests/test_unfamiliar_schema.py`.
The fixture first discovers randomized opaque identifiers using the governed
metadata reader. It proposes a minimum sample, obtains separate fixture-owned
record authorization, evaluates only acquired fields, and requests another
unresolved scope. Independent scenarios change only evidence to reverse date
ordering, contradict an earlier result, retain ambiguity, and test authorization,
provenance, replay, budgets and tenant boundaries. All transports are local.

## Evidence-reference checkpoints

`role_checkpoint.checkpoint_study(study)` returns bounded canonical JSON (64 KiB
maximum) containing the tenant/company/source binding, evaluator format version,
schema evidence ID/hash and at most 100 record evidence ID/hash/request-hash
references. It contains no copied record values, grants, credentials or saved
classifications. The fingerprint includes acquisition timing, upstream identity
and provenance. Tuple-based immutable metadata needs a dedicated fingerprint
encoding; the legacy historical serializer only accepts its narrower API-record
shape and is deliberately unchanged.

`restore_study(payload, expected_sha256=..., tenant_id=..., company=...,
source_id=..., evidence_lookup=...)` resolves original observations and acquisition
scopes from the trusted archive. It checks all hashes and bindings, then replays
ordinary study admission and recomputes claims. Duplicate/unknown JSON keys,
duplicate evidence IDs, unknown evaluator versions, oversized checkpoints and
missing/changed evidence fail closed. Current claims and checkpoint creation now
also reject acquisition scopes changed in the archive after initial ingestion.

A caller may persist the returned JSON using its existing local storage policy.
It must retain the expected digest in a separately trusted checkpoint index;
a hash beside attacker-writable content does not establish authenticity. Selecting
an old snapshot as the current one is rejected when the current digest is pinned.
There is no automatic latest-revision selector, signed index, production archive
or new storage service. Tests persist successive snapshots to local temporary
files, reopen them and prove that earlier and contradictory revisions reproduce
their respective conclusions and that unchanged planning stays deterministic.

Checkpoint restoration performs no collection, restores no grant and cannot
make a metadata authorization into record authority. The evidence archive must
remain available; these reference checkpoints are not standalone evidence backups.
Semantic business-role validation remains outside what these sample predicates
can legitimately establish.
