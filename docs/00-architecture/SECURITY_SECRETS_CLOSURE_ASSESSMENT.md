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
