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
| RUNTIME_HEALTH | PARTIAL | Existing canonical local staging services were active; local Command Center health and ready, and report-host health returned 200. The live local TLS certificate verified for localhost and denied a wrong hostname, but its replacement file on disk is expired. Exact candidate has not passed supervised deployment or authenticated browser recovery. |
| INTELLIGENCE_EVALUATION | NOT_PROVEN | Synthetic benchmark #232 reports zero incremental validated discoveries, zero verified customer savings. Draft #254 hybrid ties conventional controls. |
| CUSTOMER_PILOT_READINESS | NO_GO | No fresh customer authorization, qualified login and enrollment service, or independently adjudicated business value. |
| PRODUCTION_RELEASE_READINESS | NO_GO | No independent release decision, owner authorization, production runtime qualification or customer security approval. |

| Blocker | Priority | Status | Component | Root cause and evidence | Remediation / owner | Verification |
|---|---|---|---|---|---|---|
| F-01 | P1 | OPEN | release qualification | Exact #248 candidate lacks stable-Linux full pytest; repository-wide Ruff had 147 findings at the base. Draft #261 scopes 146 historical hash-bound review findings out of active lint and waives one intentionally naive negative test; Ruff check . passes there. Parent #241 Ubuntu suite cannot qualify the changed release tree. | Integrate reviewed draft #261, then run the full suite and Ruff on the exact final head with owner-approved stable-Linux qualification. Owner: implementation engineer. | draft #261 Ruff and focused tests pass; exact integrated full suite pending |
| F-02 | P1 | OPEN | release governance | No independent exact-tree security and architecture decisions or owner approval exist. Draft gate #249 denies promotion without trusted detached approvals. | Obtain distinct trusted reviewer decisions on final immutable manifest, then owner release decision. Owner: independent reviewers and owner. | pending |
| F-03 | P1 | OPEN | candidate deployment and recovery | The existing canonical staging is not an installed #248 release candidate. Draft #252 isolated report-host drill passed; actual supervised fresh deployment and authenticated browser recovery remain untested. | Qualify exact reviewed artifacts in an isolated supervised deployment with TLS, auth, failure, restart and browser checks. Owner: implementation engineer and deployment reviewer. | partial isolated drill |
| F-04 | P1 | OPEN | customer onboarding | Offline identity and tenant controls lack configured OIDC code flow, protected HTTP session, external credential vault and independently reviewed enrollment endpoints. Drafts #250/#251 implement offline boundaries only; no customer-facing service is exposed. | Implement and review protected login, credential enrollment, destination policy and authorization wizard before customer activation. Owner: implementation engineer; owner selects IdP and vault. | focused offline tests only |
| F-05 | P3 | OPEN | customer value | Synthetic labels and costs cannot establish recoverability, realized savings or willingness to pay. #232 and drafts #253/#254 report zero incremental validated discoveries and null ROI. | Use a separately authorized, independently adjudicated read-only pilot with conventional baseline and real cost evidence. Owner: owner, customer reviewer and product lead. | not established |
| F-06 | P1 | OPEN | existing local TLS facade recovery | The certificate file loaded on the next proxy restart expired at 2026-10-17T00:15:23Z; the user timer restarts the proxy but does not renew that file, and the separate renewal script imports an absent /tmp worktree. Disk certificate fingerprint B5:37:87:A5:11:73:EF:1F:75:CE:BB:A5:E6:36:F4:E6:DC:49:A9:47:A9:A4:FE:DC:AF:6E:AB:32:FB:F8:23:30 is expired. Running proxy presents fingerprint 35:55:BD:30:07:8A:66:02:6A:21:A1:D8:30:3F:BF:85:41:11:1E:62:15:84:EB:89:F8:42:3E:B0:B3:83:00:87, valid until 2026-11-15T17:45:23Z; TLS /ready returned 200 with live cert explicitly trusted and wrong hostname failed. No service was changed. | Prepare durable certificate renewal from stable source, atomically replace certificate and key, verify trust and hostname, then perform one controlled proxy restart after explicit staging-change authorization. Owner: deployment operator and owner. | read-only diagnosis; correction pending staging-change authority |

All service identities and test results must be refreshed on the final
integrated SHA and tree. `execution_allowed=false` remains enforced.
