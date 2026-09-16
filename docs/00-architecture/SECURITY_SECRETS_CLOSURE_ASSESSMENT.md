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

## Single next step

The operations owner must provision one disposable capable candidate host and
commission one joint independent security/custody qualification execution at
commit `b3a90e8bdcd363443371668ce0c59f0a62dd9297`: retain the corrected wheel,
`RECORD` and profile hashes; execute the existing installed suite once with two
synthetic credential generations; capture the namespace, capability, mount,
descriptor, nft/source-counter, restart and cutoff-first rotation evidence listed
above; and return a signed accept/reject matrix to the architecture/release
authority.
