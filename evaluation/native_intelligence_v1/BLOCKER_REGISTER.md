# Blocker register

| ID | Severity | Reproduction and root cause | Correction / evidence | State |
| --- | --- | --- | --- | --- |
| NI-001 | P1 evaluator integrity | A modified `calibration.json` could supply its own evidence UUIDs and pass the benchmark's membership verifier. The earlier harness did not recompute training calibration. | `run_benchmark` now recalculates calibration from pinned training cases and labels and compares the complete object; regression test for forged prior. Source policy and holdout outcomes unchanged. | Resolved in this draft, pending final exact-head suite |
| NI-007 | P1 package boundary | The first core sdist included held-out cases and labels because its broad default source inclusion covered `evaluation/native_intelligence_v1`. This could expose the evaluation oracle to an installed source build. | Exclude the full research lab from the core sdist and test a built archive for the selector source and absence of lab files; rebuild all artifacts at the corrected head. | Resolved in this draft, pending final exact-head suite |
| NI-002 | Scientific | Current planner's alphabetical record order found only 7/21 held-out designed positives; its observed structure lacks the outcome likelihood needed to prioritize questions. | Experimental Bayesian module and equal-access lab added. Strong heuristic ties its discoveries; no product adoption. | Not a qualified product improvement |
| NI-003 | Scientific | Transfer likelihood shift produces four enhanced false positives. | Preserve failure cases; propose independent shift/abstention experiment. | Open research limitation |
| NI-004 | External gate | No independently appointed procurement/finance reviewer or verified customer outcomes. | Synthetic evidence packet and labels separated; no reviewer judgment fabricated. | Owner/reviewer controlled |
| NI-005 | Environment gate | No authorized stable Ubuntu runner. WSL2 suite is diagnostic and cannot establish stable-Linux qualification. | Exact source and full WSL test logs to be preserved; request runner authorization when available. | Owner controlled |
| NI-006 | Product gate | No approved customer authorization or real ERP scope; proposed Terrace Beirut identifiers are not access approval. | Keep customer pilot NO_GO; no customer contact. | Owner/customer controlled |

No canonical ref, staging service, public tunnel, live ERP, Actions setting, signing key, or package registry is changed by this draft.
