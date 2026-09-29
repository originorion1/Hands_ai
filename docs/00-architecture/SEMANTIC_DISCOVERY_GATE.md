# Independent semantic evidence gate

Issue #133 extends the role studies on PR #128, process evidence on #130 and
reference recovery on #132. Existing readers, permits, grants and transport policy
are unchanged. This is an offline semantic gate, not live-pilot promotion.

## What changed

`SemanticStudy` composes the canonical `RoleStudy`, `Observation`, `Evidence`,
`Hypothesis`, `ObservationRequirement`, and graph types. It compares every
structurally eligible discovered field with independently instrumented process
anchors. It has no collection, grant, execution or write interface.

`SemanticRule` is a bounded, machine-readable reviewed policy: candidate kind,
required evidence class/channel/dimension combinations, minimum independent
collectors, minimum distinct subjects, counterexample invalidation and explicit
unknown conditions. Extending the vocabulary requires another reviewed rule,
not a change to the evaluator or a customer field mapping. Rule text is not
executable code. External text cannot register rules.

The six initial contracts are monetary measure, physical measure, completion date,
recording date, recipient reference and originator reference. These are narrow
measurement/process roles. A monetary measure is **not** automatically revenue,
a price, an account balance, or a particular accounting treatment. No restaurant
ontology or KPI formula is introduced.

## Epistemic contract

| State | Meaning |
|---|---|
| FACT | An admitted structural declaration or process/record observation, retaining its origin and acquisition scope; not a guarantee that the source is correct. |
| HYPOTHESIS | Canonical possible role generated for each eligible opaque field. |
| VALIDATED | Every explicit rule requirement matches at least two distinct subjects, with at least two reviewed independent collector domains, no counterexample and no competing validated match. Only the identified sample and rule are covered. |
| INVALIDATED | An admissible counterexample contradicts the rule in the current evidence version. Support fraction cannot overrule it. |
| UNKNOWN | Missing independent evidence, conflicting candidate matches, ambiguous source revisions, absent relationship targets or unsupported values. |

Each claim retains its canonical hypothesis ID, subject, rule, supporting,
contradicting and independent evidence IDs, unresolved alternatives, missing
requirements and reason. `support_fraction` counts supporting referenced evidence
IDs divided by their union with contradicting IDs. It is descriptive, uncalibrated
and never a promotion criterion.

All date candidates compete. No spelling, field order, earliest-date or cardinality
shortcut is used. Numeric declarations do not establish units. Reference matches
require separately admitted target records and two process/relationship witnesses;
they do not establish ownership, causation or a customer/supplier classification.

## Independent evidence boundary

The fixed anchor protocol describes a subject source/resource/record, acquisition
date, evidence class, process channel, measurement dimension and value, optional
target resource and explicit correction ancestry. It names no business field.
The engine searches all discovered candidates for evidence-backed matches.
Aggregate anchors also carry two bounded component values; their sum must agree
with the reported value. Reconciliation alone is not semantic proof.

The reviewed `Instrument` registry binds source/resource/provenance and allowed
classes. Separate record grants admit these observations through the unchanged
`launch_pilot_read`. Metadata permission never admits process records.

An `Origin` entry in the trusted admission archive binds each evidence ID to
original collector-domain/fact identities and parent evidence IDs. Re-reading a
record or changing URLs/observation IDs does not create another origin. Derived
evidence must inherit exactly its parents' roots. Shared roots are counted once
across requirements; a transformed copy cannot stand in for an independent class.
Parent provenance, scope and lineage are recursively checked and fingerprinted.

**Independence requires an independently reviewed instrumentation/lineage source.**
The engine cannot discover whether two purported collectors secretly copied each
other. The archive/registry is trusted integration configuration, not a claim made
in external record text. This implementation does not authenticate arbitrary
same-process Python or install a production collector. Fabricated archive entries
from a trusted administrator remain outside its epistemic guarantees. The live
readiness gate therefore remains blocked independently of this semantic gate.

The laboratory uses a primary process stream and a separate lifecycle/relationship
log and reconciliation components. The second collector does not read business
field values or reuse the first collector's measurement vector. Transforming one
stream into the other is separately tested and cannot validate semantics.

## Revision and recovery

Evidence admission is atomic, at most 25 anchors per batch and 100 retained anchors.
There are at most 128 hypotheses, 16 rules and eight instruments. Every evaluation
rechecks archived observations, acquisition scopes and lineage fingerprints.

Counterexamples invalidate earlier interpretations. A correction must explicitly
name a previous observation from the same instrument, preserve subject, channel,
class, dimension and original roots, and advance acquisition time. Missing,
branched or inconsistent correction ancestry is rejected. Old evidence and frozen
revision snapshots remain available; only explicitly superseded anchors leave the
current evaluation set. Contradiction is not silently deleted. Correcting a bad
observation back to its original value can reconverge with history intact.

The base record sample is pinned at first semantic admission. Changing that sample
requires a new explicitly scoped study; it cannot silently rewrite earlier
semantic history. Automatic source-record version alignment remains outside scope.

Current graph claims preserve epistemic status, rule, company/source, reason,
sample limitation and revision. Separate evidence nodes retain provenance and
timestamps. Historical claim nodes and revision relationships preserve prior
states; proposed relationships never become authorization edges.

`semantic_checkpoint.py` reuses `role_checkpoint.py`. Checkpoints contain original
evidence references/fingerprints, externally supplied policy fingerprints and
batch/revision identities. They contain no raw records, credentials or grants.
Format 2 also binds the explicit semantic evaluator version. Older formats or
different evaluators reject rather than silently migrating. Restore requires the
independently pinned checkpoint digest, exact tenant/company/
source, the original trusted archive and separately supplied matching policy.
It reconstructs every revision and recomputes claims. Missing/changed evidence,
lineage, scope, policy or revision identities fail closed. Three tests perform
this recovery in a fresh interpreter and prove that revoked continuation is denied.

A checkpoint is not authentication if its trusted digest is stored alongside
attacker-writable content. This remains a recovery interface, not a production
archive service. Fresh acquisition reproduces semantic claims; original upstream
observation IDs remain in integrity fingerprints, so independent reacquisitions
need not have identical checkpoint hashes.

## Minimum observation proposals

Before record evidence exists, planning delegates to the existing bounded
structural planner. Afterwards it selects missing evidence classes using unresolved
alternatives per field-cell cost, with extra weight for contradictions. Previously
observed subject/class/window evidence reduces the row requirement. Non-aggregate
requests omit component fields. Every proposed field requires an explicit public
sensitivity classification; missing or sensitive classification denies selection.

A proposal identifies hypotheses, missing class/channel/dimension evidence,
existing evidence IDs, rationale, source/resource/fields, inclusive window of at
most 31 days, row/cost limits and technical binding review blockers. It always
returns `authorization_required=separate_record_grant` and
`execution_allowed=false`. It does not collect, issue a grant or retry.

This is a bounded heuristic, not calibrated information gain or an optimal active
learner. Subject-specific/source-class query filters and automatic discovery of
independent instrumentation are not implemented. A proposal can remain blocked
until a reviewed acquisition can supply the requested evidence within its bound.

## Reproducible experiment

Run `python tools/semantic_discovery_gate.py` from the repository root with the
project's development dependencies installed. It runs the full and focused suites,
Ruff, compilation, capability scan, diff checks and the core demo. Each of the 20
semantic acceptance criteria must have executed passing tests; missing evidence
or any verification failure produces FAIL. It never enables live readiness.

| Organization | Executable outcome |
|---|---|
| A | Five roles converge from independent evidence: monetary measure, completion/recording dates and recipient/originator references. Physical meaning of another numeric field stays UNKNOWN. |
| B | A monetary interpretation validates, is contradicted by corrected independent process/reconciliation evidence, moves to another candidate, and can reconverge after another explicit correction. Historical states remain. |
| C | Structural predicates have perfect sample support, but business-role claims remain UNKNOWN because independent grounding is absent. |

All three have distinct structural shapes and only opaque resource/field names.
Renaming all identifiers, field reordering and irrelevant metadata preserve
semantic results. A one-anchor correction changes affected roles; independent
unaffected role claims remain stable. Malformed dates, boolean numeric tricks,
missing targets, aggregate mismatch, repeated/derived evidence, policy changes,
cross-scope injection, malicious text and missing authority are tested.

No live/customer data, credentials, endpoint configuration, paid model dependency,
production activation, merge or write authority is introduced. This finite rule
vocabulary is not general autonomous ontology discovery or validation of the full
organizational-learning thesis. Independent review remains necessary.
