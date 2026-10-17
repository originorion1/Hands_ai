# ORION release health and blocker register

Snapshot: `2026-10-17T02:09:27Z`. Candidate source:
`c631d1fe109f89360a3292784b262086ad39a4d6`; tree `4dd88a3affd62307d3c87214e82cf28c60371049`.
Canonical: `eabda90593f00ddc9b6ace6a4b9b9301fb3a01dd`. Decision:
`RELEASE_CANDIDATE_BLOCKED`.

This snapshot is evidence about draft branches, not a release approval.

| Health dimension | Status | Evidence |
|---|---|---|
| BUILD_HEALTH | PARTIAL | Exact #248 source rebuilt five wheels and five source archives twice with byte-identical hashes in private evidence custody. |
| PACKAGE_HEALTH | PARTIAL | Hash-locked offline Kernel-only and combined installs passed; vulnerability assessment and final immutable trust approval remain open. |
| TEST_HEALTH | BLOCKED | Focused candidate tests passed; no stable-Linux full pytest run on exact #248 head. Parent #241 hosted pass is not this tree. |
| SECURITY_HEALTH | BLOCKED | Independent exact-head security and architecture approval absent. Draft release gate #249 is unmerged. |
| RUNTIME_HEALTH | PARTIAL | Existing canonical local staging services were active; local Command Center health and ready, and report-host health returned 200. Exact candidate has not passed supervised deployment or authenticated browser recovery. |
| INTELLIGENCE_EVALUATION | NOT_PROVEN | Synthetic benchmark #232 reports zero incremental validated discoveries, zero verified customer savings. Draft #254 hybrid ties conventional controls. |
| CUSTOMER_PILOT_READINESS | NO_GO | No fresh customer authorization, qualified login and enrollment service, or independently adjudicated business value. |
| PRODUCTION_RELEASE_READINESS | NO_GO | No independent release decision, owner authorization, production runtime qualification or customer security approval. |

| Blocker | Priority | Status | Component | Root cause and evidence | Remediation / owner | Verification |
|---|---|---|---|---|---|---|
| F-01 | P1 | OPEN | release qualification | Exact #248 candidate lacks stable-Linux full pytest and repository-wide Ruff has 147 pre-existing findings at #241/#248. Focused tests and scoped Ruff passed; prior parent Ubuntu suite cannot qualify changed tree. | Run full suite on exact head with owner-approved Actions; isolate and correct applicable Ruff findings on a separately scoped issue. Owner: implementation engineer. | pending |
| F-02 | P1 | OPEN | release governance | No independent exact-tree security and architecture decisions or owner approval exist. Draft gate #249 denies promotion without trusted detached approvals. | Obtain distinct trusted reviewer decisions on final immutable manifest, then owner release decision. Owner: independent reviewers and owner. | pending |
| F-03 | P1 | OPEN | candidate deployment and recovery | The existing canonical staging is not an installed #248 release candidate. Draft #252 isolated report-host drill passed; actual supervised fresh deployment and authenticated browser recovery remain untested. | Qualify exact reviewed artifacts in an isolated supervised deployment with TLS, auth, failure, restart and browser checks. Owner: implementation engineer and deployment reviewer. | partial isolated drill |
| F-04 | P1 | OPEN | customer onboarding | Offline identity and tenant controls lack configured OIDC code flow, protected HTTP session, external credential vault and independently reviewed enrollment endpoints. Drafts #250/#251 implement offline boundaries only; no customer-facing service is exposed. | Implement and review protected login, credential enrollment, destination policy and authorization wizard before customer activation. Owner: implementation engineer; owner selects IdP and vault. | focused offline tests only |
| F-05 | P3 | OPEN | customer value | Synthetic labels and costs cannot establish recoverability, realized savings or willingness to pay. #232 and drafts #253/#254 report zero incremental validated discoveries and null ROI. | Use a separately authorized, independently adjudicated read-only pilot with conventional baseline and real cost evidence. Owner: owner, customer reviewer and product lead. | not established |

All service identities and test results must be refreshed on the final
integrated SHA and tree. `execution_allowed=false` remains enforced.
