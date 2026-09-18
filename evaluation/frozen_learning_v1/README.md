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

No product evaluation was run and no prior fixture result is relabeled as
independent. The checked-in `result.json` is therefore a machine-readable
blocked result, not a score.

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
7. independent reviewer evidence verifying those authorship and chronology
   facts; and
8. distinct synthetic source and authorization domains for discovery,
   instruments, development outcomes, and evaluation outcomes.

The operator is not asked to supply a business mapping. Process evidence uses
the existing canonical anchor protocol; the frozen semantic rules decide which
opaque fields, if any, it supports.

## Runnable preflight

From the repository root:

```bash
PYTHONPATH=src .venv/bin/python tools/frozen_learning_evaluation.py status
```

This verifies the frozen learner component hashes and prints the blocked result.
Exit status `2` is intentional while material is missing.

For an externally supplied package:

```bash
PYTHONPATH=src .venv/bin/python tools/frozen_learning_evaluation.py \
  preflight /path/to/package.json
```

Preflight validates the exact protocol binding, staged-material identity,
opaque identifiers, authorship disclosure/review, chronology, source/grant
separation, canonical instrument shape, absence of direct answers, fixed four
release stages, and safety flags. It returns
`READY_FOR_SINGLE_FROZEN_EVALUATION` only when those prerequisites are present.
It does not convert JSON into grants or authority and does not execute arbitrary
modules or callbacks.

After a package passes preflight, its identity must be retained and the single
evaluation must be run through the existing metadata/read adapters,
`assess_restaurant`, `begin_learning_cycle`, and `resume_learning_cycle`. The
trusted local evaluator may release only the current stage after observing the
matching durable commitment. The evaluator-only commitment and future stages
must never enter learner inputs or the prediction ledger. This is trusted local
separation, not malicious-process isolation.

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

Focused protocol/preflight checks passed: `5 passed`. The single final campaign,
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
