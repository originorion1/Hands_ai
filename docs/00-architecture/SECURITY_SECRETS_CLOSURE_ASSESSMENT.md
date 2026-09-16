# SECURITY and SECRETS closure assessment (#172)

This review is scoped to the supported installed pilot artifact at PR #173
(`9778ed322151d6033518190c355e3e3ce6e2cf21`).
It does not change release statuses, activate access, or certify production.

## Supported boundary

The only installed console entry point is `orion-runtime`, declared in
`pyproject.toml` as `orion.pilot.deployment:main`.  The public module entry
point is equivalent (`python -m orion.pilot.deployment`).  `--artifact` and
`--health` are inspection-only; `--serve` is the only deployment path and
requires a private manifest.  The supervisor validates the artifact RECORD,
private state/key directories, pinned synthetic certificate, fixed synthetic
host, and the required rootless WSL tools in
`src/orion/pilot/deployment.py:load_manifest`.  It then enters a fresh mapped
user/network namespace before loading deployment keys or starting services
(`deployment.py:main` and `isolation.py`). Missing capability denies startup;
there is no unconfined fallback.

The supervisor creates distinct audit, evidence, authorization, gateway and
acquisition services.  `deployment.py:service` mounts only the role's
capability file and required private material.  The source credential is
mounted only in the gateway role; the issuer and custody signing material are
mounted only in their respective owners.  Acquisition receives a bounded RPC
receipt, not the credential or issuer key.  The reasoner receives no private
state/key mount.  The gateway uses fixed HTTPS routes and disables redirects,
proxying and caller-selected URLs (`src/orion/pilot/gateway.py`).  Source I/O
is performed only after the existing grant, reservation and gateway redemption
checks (`src/orion/pilot/broker.py`, `broker_worker.py`, `custody.py`).

The ERPNext adapters, live-session modules and callable laboratory APIs remain
importable for development compatibility.  They are not packaged console
entry points.  Their default opener is explicitly disabled
(`src/orion/discovery/erpnext_adapter.py:_default_opener`), and direct adapter
tests verify that no built-in network read occurs.  Importability is therefore
not treated as containment; the release criterion below intentionally covers
the broader legacy surface.

## Requirement-to-evidence matrix

| Requirement | Actual implementation and location | Existing executable evidence | Supported path | Remaining gap and classification | Closure criterion |
|---|---|---|---|---|---|
| SECURITY: no same-process injection can bypass confinement, authorization, admission or egress | `orion-runtime --serve` enters mapped user/network namespaces; `process_command` uses private role mounts and cleared environment; supervisor rejects missing kernel tools and unverified manifests. Admission binds the accepted request and canonical custody state before publication (`deployment.py`, `isolation.py`, `broker.py`, `evidence_custody.py`). | PR #173 exact-tree WSL evidence: packaged runtime cases, isolation probes and full suite completed; CI run `35038397627` succeeded on the exact head. Existing `tests/test_packaged_primitives.py`, `tests/test_packaged_runtime.py`, and `tests/test_pilot_transport.py` cover startup denial, role mounts, authenticated IPC, admission binding and disabled legacy opener. | Supported installed `orion-runtime --serve` only. | **Missing independent release evidence**, not a reproduced supported-path defect: the complete gate is explicitly about legacy same-process callable injection outside the confined pilot and requires independent deployment/host attestation. Restricted kernel verification on an unavailable host remains NOT PROVEN; synthetic WSL evidence is not production proof. | Independent architecture/operations review must attest the complete deployment boundary (including the host policy and all supported launch paths) and reconcile the historical gate reason without narrowing it. Evidence must be fresh on the exact artifact and fail closed when controls are absent. |
| SECRETS: no legacy same-process credential surface is reachable by supported application processes | Credential gateway owns source credential; authorization owns issuer material; evidence/audit own their signing material; acquisition/reasoner mounts contain only capability files and IPC endpoints (`deployment.py:service`). `gateway.py` passes the credential only on an anonymous stdin pipe to fixed curl; exception paths redact private values. `erpnext_adapter.py` has no implicit opener. | PR #173 exact-tree installed tests show no credential/key values in runtime output, missing manifests deny startup, and ordinary synthetic HTTPS succeeds only through the gateway. `tests/test_pilot_transport.py` verifies disabled legacy reads; packaged runtime tests verify source I/O, revocation, stop and restart behavior. | Supported installed `orion-runtime --serve`; not arbitrary imports in a development interpreter. | **Missing independent release evidence**, not a reproduced installed-path defect: the gate names the broader legacy same-process surface and production credential custody. Host/operator/custody compromise, inherited credentials outside the reviewed manifest, and privileged rollback are trusted or unproven assumptions. | Independent custody and host review must verify role-specific access against the exact artifact and mount/namespace evidence: source credentials are available only to the gateway and its fixed credential-consuming child; issuer material only to its authorization owner; audit/evidence signing material and stores only to their respective custody owners. Acquisition and reasoning may hold only their designated bounded IPC capabilities, not source credentials, issuer keys or custody signing keys/storage. The trusted host/operator/supervisor remains outside the untrusted application boundary; its compromise is not covered by this claim. Any legacy API retained for laboratory use must remain outside the supported deployment contract; status changes require explicit architecture approval. |

The fixed readiness values in `src/orion/pilot/readiness.py:GATES` are release
declarations, not fresh detectors.  This document records the bounded installed
evidence and the unresolved complete criteria; it does not promote either gate.

## Trust boundary and limitations

Trusted components are the WSL kernel/host and operator, the installed artifact
and native namespace tools, authorization/custody/gateway services, certificate
and source truth, and the clock.  The application cannot issue grants, read
issuer/custody keys, select arbitrary destinations, bypass admission, or restore
authority after stop/restart within the reviewed installed path.  This review
does not establish protection against a privileged host/operator, compromised
custody service, whole-store rollback, or production credential handling.  It
also does not convert synthetic-source or CI results into customer deployment
attestation.

`LIVE_PILOT_READY=false` and `execution_allowed=false` remain mandatory.  The
single next prerequisite is an independent architecture/operations attestation
against the complete SECURITY and SECRETS criteria on the exact installed
artifact, followed by an explicit human reconciliation decision; no customer
access is authorized by that decision.

## Deployment qualification packet

This packet qualifies a candidate host; it does not approve the host for
production. The fresh installed-runtime record is retained from the approved
WSL run (23 passed, zero skipped, wheel SHA-256
`ac0a039f1d564c73244606973c2d436a2f495e924862254c0d8ae15c367a48fa`). It is
evidence for the synthetic deployment boundary, not a production attestation.

### Candidate-host inventory

The inspected shell runs as unprivileged `orion` (uid/gid 1000). The kernel is
WSL2 `6.6.87.2-microsoft-standard-WSL2`; the current user/net/pid/mount
namespace identities are distinct namespace handles, not host-root authority.
The retained build interpreter is
`/tmp/orion-build-163-SuBhYK/bin/python3` (Python 3.12.3, pip 24.0,
hatchling 1.27.0). The recorded tools are util-linux `unshare`/`nsenter` and
`setpriv` 2.39.3, bubblewrap 0.9.0, and curl 8.5.0. `orion-runtime` is not a
global host command; it exists only in the verified installed artifact
environment. This is desirable for this candidate review, but the eventual
operator must invoke the artifact's entry point rather than an ambient checkout.

The candidate shell cannot inspect host netlink policy (`ip` and `nft` return
`Operation not permitted`) and the repository `.git` mount is read-only. The
approved WSL execution context used for the retained 23-case run supplied the
required namespace/network operations. Therefore host policy is **not approved
by this inventory alone** and remains an external deployment check. No key,
credential, signing material or secret file contents were read or printed.

### Supported launch boundary

The packaged public entry point is `orion-runtime`, mapped to
`orion.pilot.deployment:main` in `pyproject.toml`; `python -m
orion.pilot.deployment` is the equivalent public module path. `--artifact` and
`--health` are inspection-only. `--serve` is the only acquisition startup
operation. `load_manifest` and `main` require the private manifest, exact
artifact RECORD, private custody roots, fixed synthetic destination, rootless
namespace capability and the complete native-tool set before loading keys or
starting roles. Any missing control returns a blocked result; no fallback launch
path is selected.

ERPNext adapters, live-session modules, broker/service modules and callable
laboratory helpers remain importable for development tests. They are not
console entry points and are outside the deployed application boundary. That
distinction is enforced by packaging and by the supervisor's role commands,
not by pretending that importability is impossible. An operator who launches
an internal module directly from an ambient interpreter would be outside the
supported deployment contract; preventing that misuse is an operator/host
configuration requirement, not a new application bypass flag. The complete
SECURITY gate therefore cannot be promoted on synthetic runtime evidence alone.

### SECURITY qualification procedure

| Item | Required control and existing evidence | What remains unverified | Acceptance procedure | Reviewer / authority | Classification |
|---|---|---|---|---|---|
| Kernel/process confinement | `deployment.py:main` creates the mapped user/net supervisor; `isolation.process_command` composes per-role user/pid/mount/net namespaces, cleared environment and dropped capabilities. The fresh 23-case artifact record reports all role namespace/capability checks and all IPv4/IPv6 destination denials. | Candidate-host policy, operator launch discipline and privileged-host behavior. | On the candidate host, verify tool versions and namespace capability; build the exact wheel; run `orion-runtime --artifact`, malformed/missing-manifest startup, and the existing full packaged-runtime suite. Capture namespace descriptors, fixed nft policy, denied alternate ports/proxies/redirect routes, and fail-closed startup. Any unavailable kernel operation is NOT PROVEN. | Independent security reviewer; release/architecture authority must approve the evidence. No named approver or approval has been supplied. | Host configuration plus independent review. |
| Admission and source egress | `broker.py`, `broker_worker.py`, `custody.py` and `gateway.py` require exact grants/reservations before source I/O; gateway uses fixed routes and no redirect/proxy/caller URL. Fresh cases report unauthorized-before-I/O and source-unmodified checks. | Production gateway/source lifecycle and operator-controlled policy outside the synthetic fabric. | Repeat the existing artifact cases with a fresh manifest and synthetic source; verify source request counters remain unchanged for denied calls, and verify kernel counters for every unapproved destination. | Independent security reviewer; architecture authority adjudicates scope. | Existing code control; production verification remains independent review. |
| Host launch surface | Only the packaged supervisor is supported. Internal modules are retained laboratory APIs and are denied when attempted from confined application roles. | Whether production operators can invoke ambient internal modules or mount a checkout. | Deployment runbook must permit only the immutable wheel entry point, forbid checkout/PYTHONPATH launches, and record the exact artifact hash. A launch outside that procedure rejects qualification. | Operations owner plus independent security reviewer; human release authority approves the runbook. | Operational procedure / host configuration. |

### SECRETS qualification procedure

| Item | Required role-specific control and existing evidence | What remains unverified | Acceptance procedure | Reviewer / authority | Classification |
|---|---|---|---|---|---|
| Source credential | `deployment.py:service` mounts `keys/source-credential` only in the gateway role. `gateway.py` sends it only through the fixed curl child's anonymous stdin. The 23-case record reports no keys in output and authorized ordinary bearer I/O only. | Production secret-store integration, host administrator access and credential rotation/erasure evidence. | Inspect role mount manifests and process file descriptors without reading values; verify acquisition/reasoning namespaces have no credential path; run the installed suite with a synthetic credential and assert it never appears in output, environment, argv or admitted evidence. | Independent secrets/custody reviewer; release authority must accept the custody evidence. | Custody/host configuration plus independent review. |
| Grant issuer material | `keys/issuer` is mounted only in the authorization owner; acquisition/reasoning receive capability IPC, not issuer material. Existing security case reports issuer-read, forgery and scope-expansion denials. | Independent issuer-key ownership, rotation and backup/rollback controls in production. | Verify the authorization role's mount and owner, deny all other role path reads, perform only the existing non-destructive negative checks, and record custody service identity and key lifecycle policy without printing values. | Independent authorization/custody reviewer; architecture authority approves the control interpretation. | Operational custody evidence / independent review. |
| Audit/evidence signing and stores | Audit and evidence roles receive their respective signing key and writable store; other roles receive neither. Existing audit/evidence cases report mutation, rollback and custody-loss denial. | Protected production storage, independent monotonic rollback witness and privileged-service compromise. | Inspect ownership/mode/mount policy, perform append/restart/custody-loss acceptance cases, verify history continuity and reject any unavailable custody service. Do not treat MAC integrity as protection from privileged rollback. | Independent audit/custody reviewer; human release authority decides whether production evidence is sufficient. | Protected storage requirement / independent review. |
| Acquisition/reasoning secrets | Acquisition receives only auth/gateway capabilities and endpoint mounts; reasoning has no private key/store mount. Existing security case reports zero actual capabilities, cleared environments and absent control credentials. | Host-level operator compromise and any unreviewed deployment wrapper. | Start from the immutable wheel, inspect role mounts and environment, run the existing security case, and reject if any source credential, issuer key, signing key or store path is visible. | Independent security reviewer; release authority approves only exact-artifact evidence. | Existing code control; host verification remains required. |

### Finite outstanding decisions

1. A candidate-host administrator must provide a versioned deployment profile
   proving rootless namespace/netlink/nft capability and exact operator launch
   restrictions. The current shell inventory is not that approval.
2. An independent security reviewer must inspect the complete installed launch
   surface, including the explicit exclusion of laboratory modules and the
   absence of ambient checkout/PYTHONPATH execution.
3. An independent secrets/custody reviewer must inspect role-specific mounts,
   storage ownership, issuer/signing-key lifecycle, rotation and rollback
   controls without receiving secret values.
4. The architecture/release authority must adjudicate those review records
   against the unchanged complete SECURITY and SECRETS criteria. No such
   external decision is present, so both gates remain unresolved.
5. Only after items 1–4 are independently satisfied may a human maintainer
   decide whether the historical FAIL reasons can be reconciled. This packet
   itself does not promote a gate or authorize customer access.

No additional technical defect was established by this qualification review.
The remaining deficiencies are host configuration, operational custody and
independent review requirements, not a missing ORION code change.
