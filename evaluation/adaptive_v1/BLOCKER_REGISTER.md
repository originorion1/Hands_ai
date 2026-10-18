# Adaptive research blocker register

| ID | Severity | Evidence and root cause | Resolution or gate |
| --- | --- | --- | --- |
| AR-01 | P1 research | PR #282 enhanced and heuristic had identical candidate sets on the old sealed splits; 27 positive misses included 10 cases with no qualifying authorized two-read pair. Entropy ranking cannot repair absent evidence. | Post-seal case-by-case analysis recorded in `FAILURE_ROOT_CAUSES.md`; test decision-risk ranking only on new cases. |
| AR-02 | P1 research | New hybrid incurs six extra false positives in unseen_b at equal access; preregistered aggregate utility -6 and paired interval crosses zero. | No promotion or retuning after holdout. Heuristic stays preferred; next drift-abstention experiment specified. |
| AR-03 | P1 qualification | No independent reviewer is appointed; designed labels are not validated outcomes. | Versioned update gate implemented and mock-tested, but update activation and learning proof remain pending external adjudication. |
| AR-04 | P1 qualification | No authorized stable Ubuntu host is available; WSL2 cannot qualify stable Linux. | Local full regression is diagnostic. Stable-Linux gate remains open for an authorized runner. |
| AR-05 | P1 package boundary | Adding an evaluator-only lab to the repository could include unseen labels in the core source archive. | `pyproject.toml` excludes `evaluation/adaptive_v1/**`; package-boundary regression inspects the built sdist. |
| AR-06 | P1 chronology | An outcome review could refer to an uncommitted or stale strategy prediction; changing the frozen ledger source also invalidated its pinned evaluation hash. | Restored the ledger source byte-for-byte. A read-only adapter in the new research module checks the persisted prospective pair and strategy version; negative regressions cover missing, changed, stale, replayed and foreign-tenant records. |

No live customer access, write permission, Actions, merge, signing or
production service activation is authorized by this register.
