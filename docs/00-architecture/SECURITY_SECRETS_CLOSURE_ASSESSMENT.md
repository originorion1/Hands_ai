# SECURITY and SECRETS closure assessment (#172)

## Reassessment target and verdict

This reassessment is for draft PR #175 at commit
`b3a90e8bdcd363443371668ce0c59f0a62dd9297`, tree
`dcd8882aa03f5b0481b11f9ace00a62139349b22`. The head was checked before this
review. The corrected code closes the previously identified restart-revalidation
and installed credential-rotation defects. No remaining implementation defect is
established for the supported installed synthetic deployment boundary.

The complete release requirements remain unchanged in
`src/orion/pilot/readiness.py:GATES`:

| Requirement | Implementation decision | Qualification decision | Reason |
|---|---|---|---|
| SECURITY — `same_process_injection_bypass` | **ACCEPT** for the supported installed `orion-runtime --serve` boundary. | **REJECT gate promotion.** | The corrected artifact has executable confinement, admission and egress controls, but the qualification record does not yet contain a retained corrected-wheel identity, candidate-host namespace/mount/nft observations, a wheel-only launch configuration, or independent security approval. |
| SECRETS — `legacy_same_process_credential_surface` | **ACCEPT** for role-specific custody in the supported installed boundary, including restart revalidation and cutoff-first source-credential rotation. | **REJECT gate promotion.** | The qualification record does not yet contain candidate-host process/mount/file-descriptor evidence, deployed custody/storage lifecycle controls, or independent custody approval. Pending acquisitions also have no recovery contract; that limitation must be accepted as a constrained-pilot operating invariant or resolved by a separate architecture decision. |

These decisions do not alter the fixed gate values. `LIVE_PILOT_READY=false` and
`execution_allowed=false` remain mandatory.

## Evidence provenance

No successful test was repeated for this reassessment.

### Fresh checks for this reassessment

- Read-only head verification established commit
  `b3a90e8bdcd363443371668ce0c59f0a62dd9297` and tree
  `dcd8882aa03f5b0481b11f9ace00a62139349b22`.
- Exact-head source inspection covered `pyproject.toml`,
  `deployment.py:validate_deployment_inputs`, `deployment.py:_restart`,
  `deployment_profile.py`, `isolation.py:process_command`, `gateway.py`, and the
  corrected lifecycle tests.
- GitHub Actions metadata was refreshed without rerunning work: run `35107111054`
  is successful at the exact corrected head; its workflow installed the project
  and ran the full pytest suite.

### Retained corrected-head execution evidence (not rerun)

- Retained focused installed execution `job-mu430z3r-700ac074` ran only the
  `restart_revalidation` and `rotation` cases through a built and installed wheel:
  **2 passed** in 54.69 seconds.
- Retained corrected-tree verification `job-mu43a7m2-b280432f` recorded
  **1866 passed**; Ruff, `py_compile`, demo, diff check and source/capability scan
  passed. The demo retained `execution_allowed=false`.
- The focused profile/restart regression retained from the correction run recorded
  **13 passed**, including profile mutation, stored-profile-digest mismatch and
  secret-metadata drift before reconstruction.

The installed test internally verifies the built wheel `RECORD` before startup,
but its quiet result did not retain the corrected wheel SHA-256, runtime
`record_sha256`, or exercised profile SHA-256 as qualification artifacts. The PR
description still identifies the superseded initial head and must not be used as
identity evidence for this corrected commit.

### Inherited unchanged-boundary evidence

The PR #173 WSL packet at `9778ed322151d6033518190c355e3e3ce6e2cf21`
recorded 23 installed-runtime cases with zero skips and wheel SHA-256
`ac0a039f1d564c73244606973c2d436a2f495e924862254c0d8ae15c367a48fa`.
It remains relevant only to unchanged isolation, gateway, authorization, audit,
evidence and legacy-adapter behavior. It is not artifact identity evidence for
PR #175. PR #174 at `e405a614658e41c2ac4c314d9de48f0926510281`
changed the assessment only, so its review adds no new runtime evidence.

## Supported artifact and trust boundary

`pyproject.toml` exposes one installed console entry point, `orion-runtime`, at
`orion.pilot.deployment:main`. `python -I -m orion.pilot.deployment` is the
equivalent module entry point. `--artifact` and `--health` inspect; `--serve` is
the only supported acquisition deployment path. `load_manifest` requires the
closed deployment profile, exact installed `RECORD`, private state/key roots,
protected custody placement, pinned certificate, fixed synthetic destination and
the required namespace/network tools before services start. Unavailable controls
deny startup; there is no laboratory fallback.

`deployment_profile.py:ROLE_POLICY` fixes audit, evidence, authorization, gateway,
acquisition and reasoning identities, mounts, state and network access.
`isolation.py:process_command` accepts only `orion.pilot.services`, selects the
installed isolated interpreter, clears the environment, drops all capabilities
and creates private user/pid/mount/network boundaries. `deployment.py:service`
maps each secret and writable store only to its owning role. Only the gateway has
the approved-destination network namespace.

The host/kernel, operator, supervisor, custody services, source truth, certificate
and clock are trusted. A privileged host/operator or compromised custody owner is
outside the untrusted application claim. Importable laboratory modules remain a
development compatibility surface; supported-deployment safety depends on the
wheel-only launch boundary described below, not on claiming those modules cannot
be imported.

## SECURITY requirement decisions

| Obligation | Exact implementation and verification evidence | Proposed decision | Exact remaining gap and type | Evidence required to close it | Responsible reviewer |
|---|---|---|---|---|---|
| S1. Bind execution to one installed artifact, entry point and closed runtime profile. | `pyproject.toml:[project.scripts]`; `deployment.py:artifact_identity`, `load_manifest`, `validate_deployment_inputs`; `deployment_profile.py:profile_for_manifest` and `validate_profile`. Profile tests reject entry-point, role, secret-owner, network and filesystem mutations. The corrected full suite and exact-head CI passed. | **ACCEPT implementation; REJECT qualification.** | **Verification artifact:** no retained corrected-wheel SHA-256, installed `RECORD` digest and exercised profile digest tied together in one candidate-host record. | On the candidate host, build once from the exact commit/tree; retain the wheel SHA-256; run its installed `orion-runtime --artifact`; retain the reported `record_sha256`; generate the canonical profile and retain its SHA-256; demonstrate startup denial after independently changing each binding. | Operations owner produces the record; independent security reviewer verifies the binding; architecture/release authority approves it. |
| S2. Confine every role with the declared process identity, mounts, environment and capabilities; deny when kernel controls are unavailable. | `isolation.py:Fabric`, `prerequisites`, `validate_protected_paths` and `process_command`; `deployment.py:service`. Existing primitive tests verify the installed interpreter, isolated mode, cleared environment, dropped capabilities and fixed service module. Inherited WSL cases exercised distinct role namespaces; corrected tests passed without changing these owners. | **ACCEPT implementation; REJECT qualification.** | **Host configuration and verification:** the target host's user/net/pid/mount namespace capability, bubblewrap policy, role mount tables and effective capabilities have not been captured for the corrected artifact. | From the installed corrected wheel, capture tool/kernel versions; role `/proc/<pid>/ns/*` identifiers; `CapEff`/`CapBnd`; sanitized environment; and mount tables showing only declared read-only/writable targets. Remove or deny each required tool/control in turn and retain startup-denied output. Any unavailable observation is NOT PROVEN. | Independent security reviewer, with the candidate-host operations owner supplying access and immutable logs. |
| S3. Permit source I/O only after scoped authorization/admission and only to the fixed HTTPS destination; denials must cause zero source I/O. | `broker.py`, `broker_worker.py`, `custody.py` and `gateway.py` require a durable reservation and one-use redemption. `gateway.py` fixes routes, HTTPS, port, certificate, response bounds and disables redirects/proxies. `isolation.py:firewall` has one approved tuple and default-drop counters. Corrected/inherited installed cases cover unauthorized-before-I/O, alternate destinations, redirects, revocation, stop and budget exhaustion. | **ACCEPT implementation; REJECT qualification.** | **Host verification and approval:** no corrected-artifact candidate-host record correlates synthetic source counters with the active nft rules/counters for all allowed and denied requests. | Run the existing installed suite on the candidate host with a fresh synthetic source. Retain source request counts before/after each denied case and nft rules/counter deltas for approved host/port, alternate host/port, proxy, redirect and IPv4/IPv6 paths. Demonstrate missing nft/netlink capability denies startup. | Independent security reviewer; architecture authority adjudicates whether the observed boundary satisfies the complete SECURITY criterion. |
| S4. Exclude legacy same-process callable paths from the supported deployment launch surface. | The only console script is `orion-runtime`; `process_command` accepts only `orion.pilot.services`; application roles receive no checkout or caller-selected module mount. `erpnext_adapter.py:_default_opener` denies implicit network reads, with inherited direct-adapter negative tests. Laboratory modules intentionally remain importable outside the supported boundary. | **ACCEPT code boundary; REJECT qualification.** | **Host configuration and approval:** there is no approved service definition/runbook proving operators cannot launch an ambient checkout, add `PYTHONPATH`, substitute an interpreter/module, or mount repository code into an application role. | Provide the exact service/unit/container command and filesystem policy: immutable corrected wheel only, isolated interpreter, no checkout mount, no `PYTHONPATH`, no general shell/module selector, manifest/profile path fixed. Rehearse rejection of a checkout/PYTHONPATH launch and retain the configuration plus denial. | Operations owner authors the launch control; independent security reviewer tests it; human release authority approves it. |

## SECRETS requirement decisions

| Obligation | Exact implementation and verification evidence | Proposed decision | Exact remaining gap and type | Evidence required to close it | Responsible reviewer |
|---|---|---|---|---|---|
| Q1. Keep the source credential out of acquisition/reasoning and use it only in the fixed gateway child. | `deployment_profile.py:SECRET_OWNERS` assigns `source-credential` to gateway. `deployment.py:service` mounts it at `/private/credential` only for gateway. `gateway.py:read` passes the bearer header through anonymous curl-config stdin, never argv, and uses a cleared fixed environment. Corrected installed rotation assertions ensure neither generation enters profile or audit output. | **ACCEPT implementation; REJECT qualification.** | **Host/custody configuration, verification and approval:** the candidate secret provider, file ownership/replacement policy, post-replacement erasure behavior and actual per-role descriptor/mount exposure are not recorded. | With synthetic values only, inspect `/proc/<pid>/mountinfo`, `fd`, `cmdline` and `environ` for all roles; show only gateway can resolve the credential path and no value appears in those surfaces or logs. Exercise the candidate secret provider's `0600`, single-link, regular-file atomic replacement and removal of staged/obsolete material. | Independent secrets/custody reviewer; independent security reviewer confirms process exposure; release authority accepts the record. |
| Q2. Restrict issuer and worker material to authorization custody. | `deployment_profile.py` assigns `issuer` and `worker-secret` only to authorization; `deployment.py:service` mounts them only there. Acquisition receives bounded authorization/gateway capabilities, not keys. Inherited authorization cases cover issuer-read, forgery and scope-expansion denial; corrected full regression passed. | **ACCEPT implementation; REJECT qualification.** | **Custody configuration and verification:** candidate ownership, backup, rotation and rollback controls for issuer/worker material are unspecified and no actual role mount/descriptor record exists. | Record the authorization service identity, `0600` owner, mount table and descriptors without values; prove all other roles cannot resolve/open either path; document and rehearse synthetic key replacement plus backup/rollback rejection while prior grants remain governed by existing expiry/revocation rules. | Independent authorization/custody reviewer; architecture authority approves lifecycle semantics. |
| Q3. Restrict audit/evidence signing keys and writable stores to their custody owners and detect unavailable/tampered custody. | `deployment.py:service` gives audit/evidence their respective signing key and store only. `journal.py` and `evidence_custody.py` deny mutation, rollback and custody loss. Inherited installed cases exercise append, restart, mutation and custody-loss denial; corrected full regression passed. | **ACCEPT implementation; REJECT qualification.** | **Host storage configuration, verification and approval:** no candidate protected-store ownership/mount record or independent monotonic rollback witness is supplied. MAC integrity alone does not detect a privileged rollback of both store and accepted tip. | On candidate protected storage, retain owner/mode/mount evidence; run append/restart/mutation/custody-loss cases; show history continuity; then restore an older store and accepted tip and demonstrate rejection using an independently retained monotonic witness. If no such witness exists, this row remains rejected. | Independent audit/custody reviewer; human release authority evaluates rollback evidence. |
| Q4. Give acquisition/reasoning no secret key or custody-store surface. | `deployment_profile.py:ROLE_POLICY` assigns both roles no secret references or state; `deployment.py:service` gives acquisition only scoped capabilities/endpoints and creates no reasoning service with private mounts. Corrected and inherited security cases verify cleared environments, zero effective capabilities and denial before source I/O. | **ACCEPT implementation; REJECT qualification.** | **Host verification:** no corrected-artifact candidate record shows their actual namespace mounts, descriptors, environment and denial of credential/issuer/signing/store paths. | Inspect those process surfaces on the candidate host and attempt non-destructive opens of every declared secret/store target from each role. Retain denials and verify source counters remain unchanged. | Independent security reviewer, with secrets/custody reviewer confirming the target list is complete. |
| Q5. Revalidate artifact, profile and custody metadata before restart reconstructs services; mismatch must cut off without restoring authority. | `deployment.py:_restart` calls `validate_deployment_inputs` before terminating/reconstructing services. That rechecks installed `RECORD`, canonical profile and stored digest, private/protected roots, each secret's regular-file/uid/`0600`/single-link/non-symlink metadata, and certificate pin. Unit regression covers profile, digest and mode drift. The corrected installed case changes credential mode after acquisition and observes cutoff, terminated gateway/acquisition, zero new source I/O and no custody-service reconstruction. | **ACCEPT.** | No missing code is established. This result still needs inclusion in the retained corrected-artifact/candidate-host packet under S1/S2; that is a **verification-record gap**, not another implementation requirement. | Retain the existing installed case output with the corrected wheel/`RECORD`/profile identities and process IDs on the candidate host. Do not rerun it separately from the single qualification execution. | Independent security and custody reviewers jointly verify the record. |
| Q6. Rotate an installed source credential through controlled replacement without restoring authority, changing scope or losing audit/budget/stop state; reject the obsolete generation. | The corrected installed `rotation` case performs initial authorized acquisition, ignored interrupted staging, cutoff, atomic `0600` replacement, required service restart, unarmed denial, fresh metadata/read authorization, replacement-backed acquisition, ordinary HTTPS rejection of the obsolete credential, audit/scope/budget assertions, durable stop and another restart. The profile digest remains unchanged because the profile contains references and policy, not secret values or generations. | **ACCEPT cutoff-first implementation; REJECT deployed-custody qualification.** | **Host/custody configuration and approval:** no candidate operator procedure proves coordinated cutoff, source-side switch, file replacement, erasure and restart using the chosen secret provider. | In the one candidate qualification execution, follow the exact cutoff-first sequence with two synthetic generations. Retain cutoff/process/nft state, atomic file metadata, unarmed restart, fresh authorization, source 401 for the old generation, absence of both values from outputs, unchanged scope/audit/budget state, and durable stop after rotation. | Operations/custody owner performs the procedure; independent secrets reviewer observes; independent security reviewer verifies cutoff/egress; architecture authority accepts the ordering. |
| Q7. Handle a source rejection after a durable acquisition attempt has been reserved. | `custody.AuthorizationCustody` deliberately leaves the attempt pending if no completion arrives; `broker.Broker` and restart reject that pending state. There is no cancellation, rollback or repair contract. This is fail-closed and was not misrepresented as successful in-flight rotation. | **REJECT recovery capability; ACCEPT fail-closed containment.** | **Missing architectural/operating contract and approval:** a credential invalidated before cutoff can make the pilot unavailable with a durable pending attempt. Existing code provides no safe recovery. | For the proposed constrained pilot, architecture must explicitly require operator-controlled cutoff before source invalidation, prohibit automatic retry/reset, and require stop/escalation if an unexpected 401 or pending state occurs. Operations/custody must rehearse the pre-cutoff coordination and the stop/escalation path with synthetic credentials. If independent source rotation cannot satisfy that invariant, a separate architecture issue must define pending-attempt reconciliation before qualification. | Architecture authority owns the contract; operations/custody owner owns the runbook; independent security and custody reviewers verify the rehearsal; human release authority accepts or rejects the residual availability risk. |

## Impact of the pending-acquisition limitation

The limitation is not a confidentiality or authorization bypass: source authority
is not restored, and restart fails closed. It is an availability and recovery
constraint. The proposed pilot can be considered only when its credential issuer
and operator can guarantee cutoff before invalidating the installed generation.
An unexpected source-side invalidation, ambiguous timeout after redemption, or
operator violation must terminate the pilot attempt and escalate; operators may
not delete or rewrite the durable journal to recover. A pilot whose source rotates
credentials independently cannot be qualified under the current architecture.

Production privileged rollback, customer-source lifecycle and every other
critical release gate remain outside this assessment. No customer access,
production credential, merge, activation or gate promotion is authorized.

## Prior single next step (completed in part by issues #176 and #178)

The operations owner must provision one disposable capable candidate host and
commission one joint independent security/custody qualification execution at
commit `b3a90e8bdcd363443371668ce0c59f0a62dd9297`: retain the corrected wheel,
`RECORD` and profile hashes; execute the existing installed suite once with two
synthetic credential generations; capture the namespace, capability, mount,
descriptor, nft/source-counter, restart and cutoff-first rotation evidence listed
above; and return a signed accept/reject matrix to the architecture/release
authority.

## Issue #178 exact-artifact reconciliation

This addendum reconciles the named release requirements after the wheel-only
candidate-host launch implementation. The runtime implementation commit is
`0a89b18d6d3645acae5f84cada30fbf40f824d5f`, tree
`a39fd6d0af69b093aee3ced4c09dfaa254433904`, based directly on PR #177 commit
`f6d4f3ebcced3a378644906ae7aa52f6a97f5ea3`. The final security exercise built
wheel SHA-256 `2375242bf75fd1b497dc688679497388f827df22b8d5aec92e49115130f6c7a2`,
verified 112 installed files with installation-specific `RECORD` digest
`2cc5c3d4982cdf42dcfbedc02516f4fd099343693052431a5c69c3e16ba3a98d`, and
exercised profile digest
`e975dfd935f4b56170c0617b5d305278f7ce250d083a1ec73f65861dba28fe44`.
The profile binds the absolute manifest
`/tmp/pytest-of-orion/pytest-46/test_installed_runtime_against0/manifest.json`
to that installation. These are synthetic disposable-host paths, not an
approved service deployment.

Fresh evidence means executed against the #178 implementation tree or its
byte-identical packaged source. Inherited evidence is identified explicitly and
does not become current-artifact evidence. The approved WSL inventory remains
the Ubuntu 24.04.4 / WSL2 6.6.87.2 context recorded in the PR #175 qualification
packet; it was not re-inventoried for #178. No row changes a release status or
records a reviewer approval.

### SECURITY

| REQUIREMENT | EXACT OBLIGATION | IMPLEMENTATION LOCATION | EXECUTABLE EVIDENCE | EVIDENCE COMMIT / ARTIFACT / HOST | FRESH OR INHERITED | RESULT | REMAINING PREREQUISITE AND TYPE | OWNER | EXACT CLOSURE EVIDENCE |
|---|---|---|---|---|---|---|---|---|---|
| S1 artifact binding | Use one approved non-editable installation; bind the executing module, installed interpreter and every hashed `RECORD` member to its installation prefix. | `deployment.py:artifact_identity`; `deployment_profile.py:profile_for_manifest`. | `test_wheel_contains_runtime_without_checkout_or_fixture_dependencies`; `test_installed_identity_rejects_source_tree_substitution`; artifact inspection reporting 112 verified files. | #178 implementation commit; wheel/`RECORD`/profile above; disposable WSL. | Fresh. | **SATISFIED for the application boundary; complete release requirement NOT PROVEN.** | **B configuration / D decision:** candidate service storage and interpreter ownership are not selected or independently accepted. | Candidate-host operations owner; independent security reviewer; release authority. | Retain the approved host's immutable wheel, installed tree ownership/modes, interpreter path, service definition and matching identity output; independently reproduce tamper and source/editable substitution denial. |
| S2 launch shape | Before enrollment, reconstruction or acquisition, require exact installed interpreter, `-I -m orion.pilot.deployment`, permitted operation/arguments, profile-bound absolute manifest, cwd `/`, closed loader/Python environment, and purpose-bound private handoff descriptors. | `deployment.py:validate_launch_invocation`, `main`; `deployment_profile.py` profile version 3. | Installed `security` case: valid launch; console-script mutation, omitted `-I`, extra/alternate arguments, wrong cwd, copied manifest, `PYTHONPATH`, and descriptor-less private-supervisor launch all deny before creation of the enrollment receipt or witness database. Profile mutations reject alternate module/interpreter/manifest/environment/package root. | #178 implementation commit; final wheel/`RECORD`/profile above; disposable WSL. | Fresh. | **SATISFIED for the application-enforced launch contract.** | **B configuration / D decision:** a privileged host administrator can replace interpreter, wheel and policy together; no approved candidate unit or immutable filesystem policy exists. | Operations owner configures; independent security reviewer verifies; release authority accepts the trusted-administrator boundary. | Capture the exact unit/container definition and host ownership policy, then execute the same negative matrix from that unit against the retained artifact without checkout mounts, shell/module selectors or ambient loader controls. |
| S3 confinement and required controls | Give each role only its fixed identity, mounts, IPC, capabilities and network namespace; missing kernel/tooling controls must deny startup. | `isolation.py:validate_protected_paths`, `Fabric`, `process_command`; `deployment.py:service`. | Current full run passed the installed `security` and IPv4/IPv6 cases and existing unavailable-kernel/tooling regressions. The security case asserts distinct namespaces, zero effective capabilities, declared mounts/descriptors and named network denial counters. | #178 implementation commit and final wheel; approved WSL execution. Host inventory itself is inherited from PR #175. | Fresh behavior; inherited inventory. | **SATISFIED in the synthetic capable-host execution; candidate deployment NOT PROVEN.** | **B configuration / C evidence:** candidate kernel, rootless namespace policy, bubblewrap/nft/ip tooling, role identities and protected mounts are not recorded against an approved unit. | Host operations owner; independent security reviewer. | On the selected host retain version/capability probes, per-role namespace IDs, `CapEff`/`CapBnd`, allowlisted mount/descriptor/environment observations and startup denial after each required control is withheld. |
| S4 authorization and source-I/O ordering | No source I/O before scoped authorization/admission; only the fixed TLS destination may be reached; launch denial, rollback, stop, revocation and budget denial must leave source counters unchanged. | `broker.py`, `broker_worker.py`, `custody.py`, `gateway.py`, `isolation.py:firewall`; outer launch validation precedes manifest custody use. | Current installed full/security, authorization, audit, revoke, witness rollback and network cases passed; denials assert synthetic source and named nft counters, including rollback rejection before acquisition/publication. | #178 implementation commit and final wheel on WSL; rollback mechanism inherited from PR #177 source base and freshly regressed. | Fresh regression of inherited mechanism. | **SATISFIED for the synthetic installed boundary; customer/production containment NOT PROVEN.** | **B configuration / C evidence / D approval:** no candidate nft/service configuration or independent review exists; customer access remains prohibited. | Network/security reviewer; architecture and release authorities. | Jointly retain the active candidate ruleset/counters and synthetic source counts for every allowed/denied path, then issue an explicit accept/reject review for the exact artifact and unit. |

### SECRETS

| REQUIREMENT | EXACT OBLIGATION | IMPLEMENTATION LOCATION | EXECUTABLE EVIDENCE | EVIDENCE COMMIT / ARTIFACT / HOST | FRESH OR INHERITED | RESULT | REMAINING PREREQUISITE AND TYPE | OWNER | EXACT CLOSURE EVIDENCE |
|---|---|---|---|---|---|---|---|---|---|
| Q1 source credential custody | Only gateway custody may read the source credential; it must not appear in acquisition/reasoning mounts, argv, environment, profile, audit or output. | `deployment_profile.py:SECRET_OWNERS`; `deployment.py:service`; `gateway.py:read`. | Current installed `security` and `rotation` cases passed the role-surface and value-absence assertions. | #178 final wheel on WSL; rotation semantics inherited from PR #175 and freshly regressed. | Fresh regression of inherited implementation. | **SATISFIED synthetically; deployed custody NOT PROVEN.** | **B configuration / C evidence / D approval:** candidate provider, ownership, erasure and reviewer enumeration are absent. | Secrets/custody owner and independent secrets reviewer. | With synthetic values, retain provider ownership/mode/link metadata, per-role mount/fd/cmdline/environment observations, atomic replacement and obsolete/staged material removal. |
| Q2 issuer and worker custody | Authorization alone owns issuer and worker material; other roles cannot resolve or open it. | `deployment_profile.py:SECRET_OWNERS`; `deployment.py:service`; authorization custody. | Current installed `authorization` and `security` cases passed scope/forgery and role-visible denial assertions. | #178 final wheel on WSL; underlying custody inherited. | Fresh regression of inherited implementation. | **SATISFIED synthetically; deployed lifecycle NOT PROVEN.** | **B configuration / C evidence:** provider backup, rotation and rollback policy plus actual role enumeration are absent. | Authorization/custody owner; independent custody reviewer. | Retain authorization identity and key-file/mount/descriptor metadata without values; show every other role denied; rehearse synthetic replacement and backup/rollback rejection. |
| Q3 audit, evidence and witness custody | Separate signing keys and writable stores by owner; application/custody restore must not overwrite independently retained witness state. | `deployment.py:service`; `journal.py`; `evidence_custody.py`; `progress_witness.py`. | Current installed `witness_audit_rollback`, `witness_evidence_rollback`, `witness_unavailable`, audit and evidence cases passed; older DB plus accepted tip is rejected before acquisition or graph publication. | PR #177 base mechanism at `f6d4f3e...`; freshly regressed by #178 final wheel on WSL. | Fresh regression of inherited implementation. | **SATISFIED for the same-host separate-directory threat model; whole-host rollback protection NOT PROVEN.** | **B configuration / C evidence / D approval:** candidate witness storage must be independently retained outside the application/custody restore set. Same-host co-location does not close whole-host rollback. | Storage/custody owner; independent audit/security reviewer; release authority. | Record restore-set boundaries and access controls, then independently witness restoration of both older custody DBs plus accepted tips while witness state remains intact; retain pre-source-I/O and pre-publication denial. |
| Q4 no application secret surface | Acquisition and reasoning receive no signing key, source secret or custody-store access. | `deployment_profile.py:ROLE_POLICY`; `deployment.py:service`; `isolation.py:process_command`. | Current installed `security` case passed mount, fd, environment, path-open and compromise probes with zero source I/O. | #178 final wheel on WSL. | Fresh. | **SATISFIED synthetically; reviewer enumeration NOT PROVEN.** | **C evidence:** no independent allowlisted enumeration exists for the final candidate unit. | Independent security reviewer with secrets reviewer confirming the target list. | Capture the final unit's per-role mounts/fds/environment and non-destructive open denials for every secret/store reference, with unchanged source counters. |
| Q5 restart revalidation | Revalidate artifact, profile, manifest path, custody layout and secret metadata before reconstruction; mismatch must invoke cutoff without restoring authority. | `deployment.py:_restart`, `validate_deployment_inputs`, `load_manifest`; profile version 3 adds launch bindings. | Current installed `restart_revalidation` case and profile mutation tests passed; mismatch terminates gateway/acquisition, performs no new source I/O and does not reconstruct custody first. | #178 final wheel on WSL; original fix inherited from PR #175 and launch binding fresh. | Fresh regression plus fresh launch fields. | **SATISFIED implementation.** | **C evidence / D approval:** include this result in the candidate-host record; no new code defect is established. | Independent security and custody reviewers. | Retain process IDs, cutoff result, source count and exact artifact/profile identities for the candidate execution. |
| Q6 cutoff-first credential rotation | Cut off before replacement; ignore interrupted staging; restart unarmed; require new authorization; accept replacement and have source reject obsolete credential without changing scope/audit/budget/stop state. | Existing deployment/custody/authorization/gateway lifecycle; no credential generation is added to the profile. | Current installed `rotation` case passed against the final wheel. Profile digest remains a policy/reference identity, not a credential-generation identity. | #178 final wheel on WSL; behavior inherited from PR #175 and freshly regressed. | Fresh regression of inherited implementation. | **SATISFIED synthetically; operator/provider procedure NOT PROVEN.** | **B configuration / C evidence / D approval:** selected provider and coordinated source-side procedure are absent. | Operations/custody owner; independent secrets and security reviewers; architecture authority. | Rehearse once with the selected provider and two synthetic generations, retaining cutoff, file metadata, unarmed restart, new grant, source 401 for old value, value absence, budget/audit and durable-stop observations. |
| Q7 pending acquisition | Never reset/refund a durable pending attempt merely to recover from source rejection or uncertain witness response. | `custody.AuthorizationCustody`; `broker.Broker`; restart denial. | Current rotation and witness crash-ordering regressions preserve pending attempts and consumed budgets. | Inherited PR #175/#177 behavior, freshly regressed by #178 full run. | Fresh regression of inherited limitation. | **UNSATISFIED recovery capability; SATISFIED fail-closed containment.** | **A architecture contract or D constrained-pilot decision:** no recovery contract exists. A signature cannot repair pending state. | Architecture authority; operations/custody owner; release authority. | Either open and implement a separately scoped pending-attempt reconciliation contract with crash tests, or explicitly accept a pilot rule requiring cutoff before source invalidation and stop/escalation after unexpected 401/timeout; sources that rotate independently remain ineligible. |

### DEPLOYMENT_CONFIGURATION

| REQUIREMENT | EXACT OBLIGATION | IMPLEMENTATION LOCATION | EXECUTABLE EVIDENCE | EVIDENCE COMMIT / ARTIFACT / HOST | FRESH OR INHERITED | RESULT | REMAINING PREREQUISITE AND TYPE | OWNER | EXACT CLOSURE EVIDENCE |
|---|---|---|---|---|---|---|---|---|---|
| D1 supported launch | The supported mutating launch is the exact installed interpreter using isolated module mode, fixed operation and manifest, cwd `/` and closed environment; console script remains inspection-only. | `deployment.py:validate_launch_invocation`; profile version 3; `WHEEL_ONLY_CANDIDATE_LAUNCH.md`. | Final installed security case and profile mutation suite, including zero-enrollment assertion for every rejected outer/private launch. | #178 implementation commit and final wheel/`RECORD`/profile above. | Fresh. | **SATISFIED application implementation.** | No code defect is established. | Independent security reviewer confirms evidence. | Review the exact commit/test output and final artifact bindings. |
| D2 candidate unit and artifact custody | The actual service definition must select only the approved interpreter/wheel/manifest and exclude checkout mounts, editable installs, ambient loaders and general command/module selectors. | Documented required unit shape; application rejects divergence but does not administer host ownership. | Source/editable, changed profile/artifact, launch-shape, cwd, manifest and environment denials passed. | #178 final wheel on disposable WSL; no deployed candidate unit. | Fresh negative execution; configuration absent. | **UNSATISFIED candidate configuration.** | **B configuration / C evidence / D approval.** Trusted host-administrator power remains outside the application claim. | Candidate-host operations owner; independent security reviewer; release authority. | Install the exact wheel into a root/administrator-owned non-editable prefix, provision the fixed unit and private manifest path, retain ownership/mode/configuration, and rerun only the launch acceptance/denial record through that unit. |
| D3 host control inventory | Candidate host must supply the required namespace, mount, capability, nft, IPC and storage controls and deny startup when any is unavailable. | Existing `isolation.py`, deployment profile roles and protected-path validation. | Exact current artifact passed the installed capable-host cases and existing missing-control regressions. | Behavior fresh on WSL; WSL version/capability inventory inherited from PR #175. | Mixed, explicitly separated. | **NOT PROVEN for a selected candidate host.** | **B configuration / C evidence:** no candidate has been approved or bound to the final unit. | Operations owner and independent security reviewer. | Retain allowlisted host/kernel/tool versions, prerequisite probes, service identities, mount/storage layout and one denial per missing required control against the exact installed artifact. |

### Other named release requirements and stale reasons

| REQUIREMENT | EXACT OBLIGATION | IMPLEMENTATION LOCATION | EXECUTABLE EVIDENCE | EVIDENCE COMMIT / ARTIFACT / HOST | FRESH OR INHERITED | RESULT | REMAINING PREREQUISITE AND TYPE | OWNER | EXACT CLOSURE EVIDENCE |
|---|---|---|---|---|---|---|---|---|---|
| WORLD_MODEL | Retain bounded authenticated semantic revision history and reconstruct from admitted originals without treating a graph as truth. | `SQLiteStudyCheckpointStore`; `semantic_runtime.py`; evidence custody semantic pins. | Current installed semantic/revision/independent cases passed except one transient retention timeout that passed unchanged on targeted rerun. | Mechanism inherited from issues #169/#171 and freshly regressed by #178 wheel on WSL. | Fresh regression of inherited implementation. | **Technical subset SATISFIED; complete gate NOT PROVEN.** | The reason `world_model_durable_revision_index_missing` is **stale**: the durable index exists. Accurate residual: **C/D** candidate protected-storage lifecycle and independently reviewed real collector/instrument lineage and semantic validity are unverified. | Semantic/provenance reviewers; storage owner; release authority. | Retain candidate-host append/restart/retention/rollback results for the exact artifact and an independent review of admitted collector roots/instruments and domain claims. Do not treat synthetic meaning as production validity. |
| RESTART | Fresh startup must restore only verified protected state, remain unarmed, preserve stop/budgets, and reject rollback against an independently retained witness before acquisition/publication. | Deployment restart; audit/evidence custody; `progress_witness.py`; `ROLLBACK_WITNESS_CONTRACT.md`. | Current restart, witness rollback/unavailable and semantic recovery cases passed. | PR #177 implementation inherited; fresh #178 regression on final wheel. | Fresh regression of inherited implementation. | **Same-host retained-witness implementation SATISFIED; complete gate NOT PROVEN.** | The reason `protected_checkpoint_recovery_rollback_witness_unattested` is partly stale as an implementation inventory: a witness now exists and is exercised. Residual **B/C/D** is candidate independent retention, restore rehearsal and approval; whole-host rollback remains outside the claim. | Storage/operations owner; independent audit/security reviewer; release authority. | On candidate storage, restore older audit and evidence DBs plus tips while leaving independently retained witness intact; demonstrate denial before source I/O/publication and document the unprotected whole-host threat. |
| AUDIT | Preserve authenticated attempt, failure, pending, stop, budget and lifecycle history under custody; reject tamper, rollback and unavailable custody. | `journal.py`; `custody.py`; `evidence_custody.py`; progress witness. | Current audit/evidence/rollback/rotation/restart installed cases passed. | Inherited mechanism, fresh #178 regression on final wheel. | Fresh regression of inherited implementation. | **Synthetic implementation SATISFIED; production lifecycle NOT PROVEN.** | Existing reason `protected_custody_production_lifecycle_unattested` remains factually accurate but must mean **B/C/D** owner/mode/backup/retention/monitoring/restore evidence and independent review, not a missing generic signature. | Audit/custody owner; independent audit reviewer; release authority. | Retain candidate storage configuration, authenticated append/restart/tamper/custody-loss/retention/restore observations, monitoring/alert handling and reviewer accept/reject decision. |
| PROVENANCE | Admission must retain protected source/resource/record identity, authorization, collector/fact roots and derivation parents; roots cannot be invented by source data. | Evidence custody; `semantic_runtime.py` collector registry validation; installed independent-evidence contract. | Current evidence and independent semantic cases passed exact scope/root/parent and cross-scope rejection tests. | Inherited issues #170/#171 implementation, fresh #178 regression. | Fresh regression of inherited implementation. | **Synthetic reviewed-registry contract SATISFIED; complete gate NOT PROVEN.** | Reason `protected_admission_archive_collector_root_unattested` remains accurate if expanded: **C/D** no independently reviewed real collector registry/root evidence is bound to a candidate archive/artifact; customer access remains forbidden. | Provenance/domain reviewer; custody owner; release authority. | Provide a separately reviewed, non-source-supplied collector/instrument registry and retained candidate archive entries binding each admitted observation to authentic roots/parents, then independently verify replay and cross-scope denial. |

The fixed gate values in `readiness.py:GATES` and
`LIVE_PILOT_GATE_RESULT.json` remain unchanged. Satisfied implementation rows do
not accept a release category. `LIVE_PILOT_READY=false`,
`execution_allowed=false`, and `allow_live_customer_access=false` remain the
only permitted state.

## Current single next action

The candidate-host operations owner must install the exact #178 wheel into the
existing approved disposable WSL host's administrator-owned non-editable prefix
and submit the fixed unit/service definition (exact interpreter, isolated module
command, absolute private manifest, cwd `/`, closed environment, no checkout or
general selector) for independent security review. The reviewer must execute the
existing launch acceptance/denial record through that unit and retain the
artifact/profile identities and host ownership/mount/control observations. This
is the highest-priority prerequisite because the application enforcement now
exists, but no actual candidate-host launch configuration is yet bound to it.
