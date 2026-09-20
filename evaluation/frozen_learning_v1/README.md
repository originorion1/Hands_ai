# Frozen-engine independent learning evaluation v1

This packet preregisters issue #182 against the unchanged PR #181 learner at
commit `7ab613ebe6ea4fdd0ab16a2e2d60baf23d135d62`, tree
`73776d74d32d9de72b528740aa977ba6d673daa8`.

The independent evaluation is **BLOCKED**. No independently authored synthetic
restaurant package or independently verified authorship record exists in the
repository. The existing `tests/fnb_lab.py` fixture and the learning engine were
both changed in the frozen learner commit. `tests/independent_evidence_support.py`
implements separately authorized evidence-lineage mechanics for issue #171; it
is not an independent restaurant dataset, staged future-outcome corpus, or
authorship record. A new seed, source string, branch, or execution agent would
not repair that limitation.

No independent product evaluation was run and no prior fixture result is
relabeled as independent. The checked-in `result.json` is therefore a
machine-readable blocked result, not a score. The execution bridge is now
runnable; its self-authored contract fixture is infrastructure evidence only.

Issue #184 adds a pinned, unsent author/reviewer handoff packet under
`handoff/` and an explicit scoped-test inventory in `test_lanes.json` and
`TEST_LANES.md`. These materials do not supply an independent package, review,
or evaluation result. Default pytest collection remains unfiltered.

## Frozen boundary

`protocol.json` fixes:

- the source commit/tree and SHA-256 of all 11 learner components;
- semantic evaluator and F&B assessment versions plus the exact F&B rule digest;
- unique supported-question selection and UNKNOWN behavior;
- flat and relationship-supported header/detail normalization;
- a 24-hour entity/location cohort and discovery-derived dynamic unit;
- the `0.5` frozen baseline and the two-development-case Laplace revision;
- Brier score on the same two later cohorts for both model arms;
- exact outcome/cohort/unit/horizon/evidence matching; and
- missing-outcome, abstention, safety and claim-separation rules.

Protocol SHA-256:
`92c5dcd63238dcd4429b3ea563b6413592e8277595f3be652db0fa13e5a746c1`.

This is a source-tree evaluation identity. No wheel is claimed as the evaluation
artifact. Candidate-host qualification remains a separate unresolved task.

## Required external deliverable

An independent preparer must provide one JSON package conforming to
`dataset.schema.json`. It must be fixed before execution and include:

1. opaque development and evaluation schemas with bounded historical records;
2. independently grounded canonical semantic-instrument records and origin
   lineage, without field mappings or role labels in the unfamiliar schema;
3. exactly two development and two evaluation outcome releases, each retained
   by the evaluator until the matching prediction has been durably committed;
4. no actual labels, expected scores, precomputed totals, target mappings, or
   unreleased outcomes in learner-visible material;
5. a sealed expected-result commitment retained outside learner input;
6. the preparer's identity/role, what ORION source and results they had seen,
   preparation/freeze times, and learner-visible versus evaluator-only material;
7. an available reviewer-evidence JSON document verifying those authorship and
   chronology facts, whose raw SHA-256 equals the package's
   `authorship.review.evidence_sha256`; and
8. distinct synthetic source and authorization domains for discovery,
   instruments, development outcomes, and evaluation outcomes.

The operator is not asked to supply a business mapping. Process evidence uses
the existing canonical anchor protocol; the frozen semantic rules decide which
opaque fields, if any, it supports.

The review document must contain exactly `review_version`, `dataset_id`,
`protocol_sha256`, `dataset_material_sha256`, `prepared_by`, `fixed_at`,
`reviewed_by`, `reviewed_at`, and `review_scope`. The first value must be
`orion-independent-review-evidence-v1`; every identity and time must match the
package, and the material digest must match preflight. A digest or declaration
alone does not authenticate a person. The evaluator retains the actual review
artifact and reports that identity authentication limitation.

## Runnable preflight and execution

From the repository root:

```bash
PYTHONPATH=src .venv/bin/python tools/frozen_learning_evaluation.py status
```

This verifies the frozen learner component hashes and prints the blocked result.
Exit status `2` is intentional while material is missing.

For an externally supplied package:

```bash
PYTHONPATH=src .venv/bin/python tools/frozen_learning_evaluation.py \
  preflight /path/to/package.json \
  --review-evidence /path/to/review-evidence.json
```

Preflight validates the exact protocol binding, staged-material identity,
opaque identifiers, authorship disclosure/review, chronology, source/grant
separation, canonical instrument shape, absence of direct answers, fixed four
release stages, available review-evidence binding, and safety flags. It returns
`READY_FOR_SINGLE_FROZEN_EVALUATION` only when those prerequisites are present.
Without the referenced review artifact it returns
`BLOCKED_REVIEW_EVIDENCE_UNAVAILABLE` with exit status `2`. Preflight does not
convert JSON into grants or authority and never executes arbitrary package code,
modules, or callbacks.

Run the single registered evaluation into a new state directory:

```bash
PYTHONPATH=src .venv/bin/python tools/frozen_learning_evaluation.py \
  run /path/to/package.json \
  --review-evidence /path/to/review-evidence.json \
  --authorized-id g_0123456789abcdef \
  --authorized-id g_fedcba9876543210 \
  --state-dir /path/to/new-empty-state \
  --output /path/to/result.json \
  --owner-report /path/to/owner-report.txt
```

Every package authorization reference must be repeated as a separately trusted
`--authorized-id`; omission, an extra ID, or a package/controller mismatch
fails before state creation or source I/O. The package never grants itself
authority. The trusted local controller constructs bounded synthetic
`MetadataAuthorization` and `PilotAuthorization` values through the existing
launchers. Package authorization identifiers remain references. Opaque
metadata, historical records and canonical instrument evidence are admitted
into the existing `RoleStudy`/`SemanticStudy` and F&B assessment. The bridge
then uses `begin_learning_cycle` and `resume_learning_cycle` with the canonical
prediction ledger, event queue, outcome normalization, revision and paired
score.

Before every outcome read, the controller queries the actual SQLite prediction
ledger and requires an unresolved prediction with the exact target, unit,
entity/location cohort, horizon, and frozen model arm. Only then does it advance
the synthetic clock and construct the separately bounded record grant. Future
records and the evaluator-only commitment stay in controller memory; their
commitment digest and holder are checked absent from learner databases. This is
trusted local separation, not malicious-process isolation.

`exercise` runs the same bridge only for a package that honestly declares
shared engine/fixture authorship. Its result is
`SELF_AUTHORED_INFRASTRUCTURE`, keeps `INDEPENDENT_EVALUATION=BLOCKED`, and must
not be used as a product-evaluation result.

## Execution-bridge evidence

The self-authored fixture proves the bridge mechanics, not independence:

- flat development and related header/detail evaluation evidence traverse the
  canonical discovery, semantic, assessment, prediction, admission and scoring
  paths;
- four releases follow durable commitments; their outcome reads total six
  bounded batches because each evaluation release contains a header/detail pair;
- mismatched and early releases, and a missing trusted grant, are rejected
  before an outcome reader call;
- unsupported relationship evidence returns `UNKNOWN` and releases no outcome;
- normalized totals retain record-level admission provenance;
- the frozen learner is hash-checked before and after, and drift blocks the run;
- evaluator-only commitment material is absent from ledger/event state; and
- the unfavorable result remains: frozen-prior evaluation Brier `0.25`, revised
  Brier `0.3125`, with `evaluation-1` retained as a failed prediction.

This fixture and runner share authorship. Consequently
`INDEPENDENT_EVALUATION=BLOCKED`, regardless of the bridge's successful
infrastructure exercise.

## Claim discipline

The four conclusions remain separate:

- the earlier self-authored evidence shows that the integrated learning loop can
  execute, but is not this independent evaluation;
- semantic interpretation is not proven until the frozen rules support it on
  independent material;
- predictive improvement is not measured until the paired later outcomes exist;
- economic value has no preregistered basis in the current packet and is
  `NOT_PROVEN`.

Synthetic success, if later observed, will still not prove real-organization
generalization, statistical significance, installed production containment, or
live readiness.

`execution_allowed=false`, `allow_live_customer_access=false`, and
`LIVE_PILOT_READY=false` remain fixed.

## Verification record

The earlier protocol/preflight checks passed: `5 passed`. The single earlier campaign,
`job-mu6rrwoz-d083fb88`, verified staged tree
`225a238d4adb142c1ff4adfc6021e1a6d1593f80`: Python compilation, Ruff,
`1955 passed`, the demo with `execution_allowed=false`, JSON validation, the
intentional blocked-status exit, source/capability scan, and cached/unstaged diff
checks all passed. Pytest emitted one cache-directory permission warning that
did not affect test execution.

Only this verification record and the corresponding evidence-matrix row were
updated after the campaign. No code, protocol, schema, machine result or test
changed, so the valid runtime evidence is reused for that documentation-only
update rather than repeating the campaign.

The execution-bridge focused suite passes `11` tests. Final campaign
`job-mu6xkrcq-8bc7cea9` verified staged tree
`ee28af604dd6c74ce379646aba262289d0646dbe`: Python compilation, Ruff,
`1961 passed`, the demo with `execution_allowed=false`, protocol/schema/result
JSON, intentional blocked-status exit, source/capability scan, frozen-source and
protocol-hash checks, and cached/unstaged diff checks all passed. Pytest emitted
one cache-directory permission warning that did not affect execution.

The first launch record, `job-mu6xhqib-b3b8f92b`, failed before any check ran
because its shell payload was transported with literal newline escapes and
pre-expanded variables. The corrected campaign above used the same unchanged
staged tree and is the final runtime evidence. This post-campaign paragraph and
the matching evidence-matrix row are documentation-only follow-up.
