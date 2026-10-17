# ORION architecture and hybrid intelligence blocker register

Review baseline: canonical `eabda90593f00ddc9b6ace6a4b9b9301fb3a01dd`;
architecture draft #235 `1ae9baa696a14516a555b03ebeb00f8f4feb5ba5`;
benchmark draft #232 `ef67bfe3a6c3ab41391c208957deb0d7d4842c49`.
This register is evidence about draft candidates, not permission to merge or run a customer pilot.

| ID | Priority | Evidence and root cause | Owner and smallest safe remediation | Status and verification |
|---|---|---|---|---|
| [#238](https://github.com/originorion1/Hands_ai/issues/238) | P0 integrity | At #235 `src/orion/kernel.py` appended each record separately; failure on the second could leave partial evidence. | Kernel/store port: atomic batch commit in draft #240. | CORRECTED IN DRAFT, unmerged. #240 `be7901e823236e84457aacf7fc8ab6f155142ca4`, Ubuntu run 38990739701: 2201 passed, 31 skipped, 64 subtests. Must revalidate integration tree. |
| [#239](https://github.com/originorion1/Hands_ai/issues/239) | P0 integrity | At #235 nested mutable evidence payloads could mutate after commit without a receipt. | Snapshot on commit and read in draft #241, stacked on #240. | CORRECTED IN DRAFT, unmerged. #241 `6ff29141081acba6f2eae808b3b490d6c727e3cd`, Ubuntu run 38990743145: 2205 passed, 31 skipped, 64 subtests. Must revalidate integration tree. |
| [#236](https://github.com/originorion1/Hands_ai/issues/236) | P1 release review | Source artifacts and package custody need independent security and architecture signoff on the final integrated tree. | Human independent reviewer and integration owner: inspect exact artifacts and merge order. | OPEN. This packet rebuilt #235 ten artifacts twice; clean Kernel-only and combined offline installs passed, with 55 focused tests. These checks are not independent approval. |
| [#237](https://github.com/originorion1/Hands_ai/issues/237) | P3 intelligence | Frozen synthetic baseline detects all eight designed positives and applicable ORION one; no incremental validated discovery or learning outcome. | Offline hybrid comparison in draft #254. | EVALUATED, no runtime promotion. Hybrid ties baseline at 8 TP/1 FP; six focused tests passed. Customer value remains unverified. |

External gates: GitHub Actions remains disabled pending exact-head owner approval for new
hosted runs; no customer-specific pilot authorization or independently adjudicated
economic outcome exists. These are approval and evidence gates, not patchable defects.
