# Native intelligence v1 release packet

## Source and scope

This draft stacks on PR #281 (`fb4faab905554476a8bce253eb1cc6dc0e5882fb`), which stacks on the security-corrected PR #280. Canonical `laboratory/orion-v0.1` remains unchanged. The pre-holdout policy and laboratory freeze is commit `b09d37bffb6b755b3973dc351023f0bef8ed8c59`, tree `e073e5c92fb7034ba8c56e8371d619e3bec8e703`. The final draft SHA/tree, environment identity, commands, package artifacts and complete logs belong in the external exact-head `QUALIFICATION_PACKET.md` and `MANIFEST.json` under `/home/orion/evidence/orion-native-intelligence-v1/`; update them only after the final commit.

New production code is limited to a domain-neutral, proposal-only finite information-gain scorer. The evaluator-only lab and all reports are under `evaluation/native_intelligence_v1`. The existing nine-positive study-selection benchmark and its labels are unchanged. No selector is wired into production grant, connector, report, knowledge promotion, or execution paths. No frontend code changed.

## Sealed evaluation artifacts

Holdout predictions SHA-256 `79af65905c20fea4fef45af3e28b8c453ddb519abca958e09e78e3984d2d68ee`; transfer predictions SHA-256 `074180cae4261c42119523fd255132994467bd08961992125409a4f4451fe2e7`. Predictions were sealed before label scoring. The corrected evaluator integrity gate recomputes unchanged training calibration; it must reproduce the same predictions apart from host-dependent `compute_ns`. Paired analysis is post-seal only. The designed holdout and transfer case outcomes are not independent customer results.

## Acceptance and release gates

Native improvement is **INCONCLUSIVE**: the enhanced selector improves synthetic calibration over existing record order, yet ties a strong heuristic on discoveries, has wide paired hit uncertainty, and produces four false positives under shifted transfer. Scientific reasoning, outcome learning, and cross-domain transfer are **NOT_PROVEN**. Net economic value is **NOT_ESTABLISHED**. No stable-Linux host or independent reviewer is authorized. Customer pilot and production release remain **NO_GO**. The experiment may be reviewed as a draft research candidate; it should not be promoted as an intelligence-qualified Kernel change.

Verification checklist for the exact draft: focused tests; full pytest; Python compilation; Ruff; `git diff --check`; demo with `execution_allowed=false`; source/capability scans; wheel and sdist build/isolated install; frontend checks only if inherited integration changes; package/import independence; WSL environment identity. WSL2 results must be labeled diagnostic until an authorized stable Linux run is available. Preserve inherited staging and PR evidence; do not merge, publish, deploy or enable Actions.
