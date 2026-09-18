# Requirement-to-evidence matrix

Fresh evidence in this table refers only to issue #182 protocol/preflight checks.
PR #181 runtime evidence is inherited and is not presented as an independent
evaluation.

| Requirement | Existing test/evidence | Commit or artifact | Relevance to current tree | New or affected check | Final status |
| --- | --- | --- | --- | --- | --- |
| Establish exact base, clean worktree, ancestry, PR and CI | Git inspection; PR #181 Actions run `35297630608` | `7ab613ebe6ea4fdd0ab16a2e2d60baf23d135d62`; tree `73776d74d32d9de72b528740aa977ba6d673daa8` | Exact frozen base; learner files are unchanged by #182 | Frozen component-hash preflight | SATISFIED |
| Avoid duplicate issue/job/PR | Issue and job registry inspection | Issue #182; completed job `job-mu66mxbc-1859c830` belongs to #180 | No prior issue owns this independent evaluation; no evaluation job was active | One issue/branch only | SATISFIED |
| Freeze semantic rules and selection/normalization/prediction/revision/scoring | Canonical source and constants | `protocol.json`; protocol SHA `92c5dcd63238dcd4429b3ea563b6413592e8277595f3be652db0fa13e5a746c1` | Binds 11 component hashes, rule digest and exact policy | `test_protocol_freezes_exact_pr181_learner_components` | SATISFIED |
| Define claim, inputs, UNKNOWN, target/unit/cohort/horizon, cutoff, baseline, revision, metric and outcome handling before execution | Machine-readable protocol | Same protocol artifact | Fixed before any independent dataset or outcome exists | Protocol JSON parse and focused assertions | SATISFIED |
| Separate loop, semantics, prediction, revision, economic value and generalization claims | Protocol criteria; blocked result | `protocol.json`, `result.json`, owner report | Prevents loop execution evidence from proving stronger claims | `test_missing_independent_material_is_blocked_without_product_claims` | SATISFIED |
| Independently authored synthetic restaurant material | No suitable package or authorship record found; current F&B fixture and engine share frozen-commit authorship | `tests/fnb_lab.py`, `src/orion/business/fnb.py`, `src/orion/learning/organizational_cycle.py` at `7ab613e...` | Existing material cannot qualify itself as independent | Schema requires external authorship and independent review | BLOCKED |
| No mappings, roles, totals, labels, future outcomes or answer-encoding identifiers in learner input | Existing opaque-schema mechanism; new package contract | `dataset.schema.json` SHA `f5b119d6cbc881f792c62e1b1377ab4221a808ca05fa28c320bdd51a5dfaa20e` | Applies to any external package | Answer-bearing record denial and opaque-identifier preflight | SATISFIED FOR CONTRACT; NOT EXECUTED ON DATASET |
| Honest independence disclosure | No current disclosure can establish independence | Dataset authorship/review section | Exact missing external evidence is named | Self-authored/unreviewed package denial | SATISFIED FOR CONTRACT; BLOCKED FOR EVALUATION |
| Learner/evaluator separation and staged future release | #180 proves commitment ordering in a shared-authorship fixture | Inherited PR #181 evidence at `7ab613e...` | Mechanics remain relevant; independence is not inherited | Schema fixes four releases; preflight verifies chronology and distinct grants/sources | NOT EXECUTED ON INDEPENDENT MATERIAL |
| Existing governed discovery, semantic, prediction, outcome and recovery interfaces | #180 focused and full regression; exact-head CI | PR #181, final campaign `job-mu66mxbc-1859c830`: `1950 passed` | Learner unchanged; recovery path is not affected by #182 | No duplicate lifecycle test | INHERITED PASS FOR ENGINE; NOT INDEPENDENT EVALUATION |
| Reconstruct outcomes from records rather than totals | Header/detail and flat normalization tests | PR #181 exact head | Frozen normalization is bound by hash | Package rejects precomputed totals; future run must use admitted record batches | INHERITED PASS FOR ENGINE; NOT EXECUTED ON DATASET |
| Score revised and prior methods on identical later observations | Cohort-safe paired scoring tests and #180 result | PR #181 exact head | Scoring implementation unchanged | Metric and eligibility frozen in protocol | INHERITED PASS FOR ENGINE; NO CURRENT SCORE |
| Retain failures, pending cases, abstentions, contradictions and exclusions | Ledger/cycle tests and protocol handling | PR #181 exact head; `protocol.json` | Existing persistence retained | Blocked result records UNKNOWN/insufficient evidence without imputation | PARTIAL; DATASET EXECUTION BLOCKED |
| Machine-readable result and concise owner report | New blocked artifacts | `result.json`, `OWNER_REPORT.md` | Accurately records no evaluation result | Focused blocked-result test | SATISFIED FOR CURRENT BLOCKED STATE |
| Safety flags and no customer/live/write authority | Existing engine and demo; new artifacts | PR #181 exact-head demo plus issue #182 packet | Unchanged | Focused blocked-result assertions | SATISFIED |
| Repository-required final verification for code changes | Final campaign `job-mu6rrwoz-d083fb88`: compilation PASS, Ruff PASS, `1955 passed`, demo `execution_allowed=false`, JSON/status checks PASS, capability scan PASS, diff checks PASS | Verified staged tree `225a238d4adb142c1ff4adfc6021e1a6d1593f80` | Fresh evidence for the unchanged code, protocol, schema, result and tests; this row was updated afterward as documentation-only evidence | No duplicate campaign; validate the evidence-only documentation diff | SATISFIED |

## Exact unresolved prerequisite

The missing deliverable is not a business mapping or a favorable answer. It is
one independently authored, chronologically frozen package conforming to
`dataset.schema.json`, plus independent reviewer evidence for its authorship,
prior ORION exposure, freeze time, learner-visible stages and retained
evaluator-only commitment. Until that exists, `INDEPENDENT_EVALUATION=BLOCKED`.
