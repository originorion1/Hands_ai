# Requirement-to-evidence matrix

Fresh evidence distinguishes the current execution-bridge checks from issue
#182's earlier protocol/preflight checks. PR #181 runtime evidence is inherited
and is not presented as an independent evaluation.

| Requirement | Existing test/evidence | Commit or artifact | Relevance to current tree | New or affected check | Final status |
| --- | --- | --- | --- | --- | --- |
| Establish exact base, clean worktree, ancestry, PR and CI | Git inspection; PR #181 Actions run `35297630608` | `7ab613ebe6ea4fdd0ab16a2e2d60baf23d135d62`; tree `73776d74d32d9de72b528740aa977ba6d673daa8` | Exact frozen base; learner files are unchanged by #182 | Frozen component-hash preflight | SATISFIED |
| Avoid duplicate issue/job/PR | Issue and job registry inspection | Issue #182; completed job `job-mu66mxbc-1859c830` belongs to #180 | No prior issue owns this independent evaluation; no evaluation job was active | One issue/branch only | SATISFIED |
| Freeze semantic rules and selection/normalization/prediction/revision/scoring | Canonical source and constants | `protocol.json`; protocol SHA `92c5dcd63238dcd4429b3ea563b6413592e8277595f3be652db0fa13e5a746c1` | Binds 11 component hashes, rule digest and exact policy | `test_protocol_freezes_exact_pr181_learner_components` | SATISFIED |
| Define claim, inputs, UNKNOWN, target/unit/cohort/horizon, cutoff, baseline, revision, metric and outcome handling before execution | Machine-readable protocol | Same protocol artifact | Fixed before any independent dataset or outcome exists | Protocol JSON parse and focused assertions | SATISFIED |
| Separate loop, semantics, prediction, revision, economic value and generalization claims | Protocol criteria; blocked result | `protocol.json`, `result.json`, owner report | Prevents loop execution evidence from proving stronger claims | `test_missing_independent_material_is_blocked_without_product_claims` | SATISFIED |
| Independently authored synthetic restaurant material | Repository search found no suitable package or authorship-review artifact; current F&B fixture and engine share frozen-commit authorship | `tests/fnb_lab.py`, `src/orion/business/fnb.py`, `src/orion/learning/organizational_cycle.py` at `7ab613e...` | Existing material cannot qualify itself as independent | Runner accepts external material only after package and review-evidence binding | BLOCKED |
| No mappings, roles, totals, labels, future outcomes or answer-encoding identifiers in learner input | Existing opaque-schema mechanism; package contract and bridge | Current `dataset.schema.json`; `tools/frozen_learning_evaluation.py` | External package supplies opaque structures, records and canonical process evidence only | Answer-bearing record denial; self-authored bridge exercise through canonical semantics | SATISFIED FOR CONTRACT AND BRIDGE; NOT EXECUTED INDEPENDENTLY |
| Honest independence disclosure | Separate review artifact is required and digest/material/identity/time bound; digest alone is not identity authentication | Dataset authorship section; `verify_review_evidence` | Self-authored fixture declares implementer/shared authorship and `NOT_PROVEN` review | Self-authored denial plus missing-review denial | SATISFIED FOR CLAIM DISCIPLINE; BLOCKED FOR EVALUATION |
| Learner/evaluator separation and staged future release | #180 ordering evidence plus new controller-side staged releases | Inherited PR #181 evidence; current execution bridge | Controller retains future records and inspects durable pending predictions before each read | Full bridge exercise; mismatch/early/missing-grant denial before outcome I/O | FRESH PASS FOR BRIDGE; NOT EXECUTED ON INDEPENDENT MATERIAL |
| Existing governed discovery, semantic, prediction, outcome and recovery interfaces | #180 focused and full regression; exact-head CI | PR #181, final campaign `job-mu66mxbc-1859c830`: `1950 passed` | Learner unchanged; recovery path is not affected by #182 | No duplicate lifecycle test | INHERITED PASS FOR ENGINE; NOT INDEPENDENT EVALUATION |
| Reconstruct outcomes from records rather than totals | Header/detail and flat normalization tests | PR #181 exact head plus current self-authored bridge fixture | Frozen normalization is bound by hash | Six admitted outcome batches reconstruct `150`, `130`, `100`, `140` with record provenance | FRESH PASS FOR BRIDGE; NOT INDEPENDENT EVIDENCE |
| Score revised and prior methods on identical later observations | Cohort-safe paired scoring tests and bridge exercise | PR #181 exact head plus current bridge | Scoring implementation unchanged | Self-authored exercise preserves prior `0.25` versus revised `0.3125` Brier | FRESH PASS FOR BRIDGE; IMPROVEMENT NOT DEMONSTRATED |
| Retain failures, pending cases, abstentions, contradictions and exclusions | Ledger/cycle tests and protocol handling | PR #181 exact head; current bridge | Existing persistence retained | Failed `evaluation-1` remains; unsupported semantics returns UNKNOWN without outcome release | FRESH PASS FOR BRIDGE; INDEPENDENT RUN BLOCKED |
| Machine-readable result and concise owner report | Blocked artifacts plus runtime `--output`/`--owner-report` | `result.json`, `OWNER_REPORT.md`, execution CLI | Checked-in artifact accurately records no independent result; external run writes exact-package results | Focused blocked-result and execution-result assertions | SATISFIED FOR CURRENT BLOCKED STATE AND RUNNER |
| Safety flags and no customer/live/write authority | Existing engine and demo; new artifacts | PR #181 exact-head demo plus issue #182 packet | Unchanged | Focused blocked-result assertions | SATISFIED |
| Execution bridge acceptance | Focused bridge suite | Staged tree `ee28af604dd6c74ce379646aba262289d0646dbe` | Exercises actual canonical learner composition without changing `src/orion` | `tests/test_frozen_learning_evaluation.py`: `11 passed` | FRESH PASS |
| Repository-required final verification for code changes | `job-mu6xkrcq-8bc7cea9`: compilation PASS, Ruff PASS, `1961 passed`, demo `execution_allowed=false`, JSON/status checks PASS, capability scan PASS, frozen-source/protocol checks PASS, diff checks PASS | Staged tree `ee28af604dd6c74ce379646aba262289d0646dbe` | Fresh final runtime evidence. Initial launcher `job-mu6xhqib-b3b8f92b` failed before checks due command transport; it is not counted as verification | Validate only this documentation-only evidence update | SATISFIED |

## Exact unresolved prerequisite

The missing deliverable is not a business mapping or a favorable answer. It is
one independently authored, chronologically frozen package conforming to
`dataset.schema.json`, plus the referenced review-evidence JSON whose raw digest
and fields bind it to that package, protocol, authorship, freeze time and review
scope. Until those artifacts exist, `INDEPENDENT_EVALUATION=BLOCKED`.

## Issue #184 handoff and test-lane evidence

| Obligation | New, existing, or inherited evidence | Status |
| --- | --- | --- |
| Pinned author/reviewer packet without fixture answers | New `handoff/` instructions and public wire contract; `MANIFEST.sha256` binds the unchanged protocol/schema and packet documents; prohibited fixture/result terms scan clean | FRESH PASS; NOT SENT |
| Truthful author/reviewer independence | New separate author and reviewer obligations; digest binding explicitly does not authenticate people; no material or review is fabricated | SATISFIED FOR HANDOFF; EXTERNAL MATERIAL BLOCKED |
| Contract feasibility and chronology | Existing `validate_package`/`verify_review_evidence` inspected; handoff documents the exact combined authorship/synthetic ordering and stop condition | SATISFIED WITH DISCLOSED V1 CONSTRAINT |
| Contract lane | New explicit nine-node selection; dry-run collected 9 and focused execution passed 9 | FRESH PASS |
| Learning lane | New existing-test selection; dry-run collected 44 and focused execution passed 44 | FRESH PASS |
| Installed lane and wheel-build reuse | Existing `tests/test_packaged_runtime.py` and module-scoped `clean_artifact`; dry-run collected 29; unchanged runtime evidence from #178/#183 remains applicable | FRESH INVENTORY; RUNTIME INHERITED, NOT RERUN |
| Full/default collection unchanged | Baseline and final dry-run node inventories both contain the same 1,961 tests; no pytest or CI configuration changed | FRESH PASS |
| Unknown/shared impact cannot omit tests | New change-to-evidence map routes unknown/shared impact to the unfiltered full lane | SATISFIED |
| Independent evaluation is outside routine tests | New lane manifest declares `pytest=false`; existing blocked result and runner remain unchanged; no independent run occurred | SATISFIED; EVALUATION BLOCKED |
| Frozen learner/protocol/schema | Existing PR #183 identities; fresh hashes and diff show no change to `src/orion`, protocol, or schema | FRESH PASS |
