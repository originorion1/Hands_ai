# One packaged, supervised read-only runtime

Issue #163 continues PR #162 exact head
`0816e735feae8304f140326a20ec990da22d0b54`. This is a deployable synthetic-only
candidate, not customer authorization or a production release.
`LIVE_PILOT_READY=false`, `execution_allowed=false`. Every release category and
critical status is retained; scanners and live-startup denial are unchanged.

## Minimum acceptance and existing contracts

Essential bounded-runtime safety is metadata-first governed discovery, a separate
exact record grant, kernel destination confinement, protected credential/grant/
audit/evidence custody, tenant/company/source binding, canonical admission and
provenance, bounded requests and durable budgets, expiry/revocation/stop, restart
unarmed, tamper detection, supervised health and fail-closed startup.

Forecasting, autonomous actions, richer commercial interpretation and multi-source
product features are not needed for a synthetic acquisition. This does not make
their existing critical requirements optional or passed. Local rows/columns are
not verified ERP protocols. Admitted-evidence revisions are not a completed
world-model revision store.

`SupervisedReadOnlyRuntime`, canonical grants/admission, `AttemptJournal`,
`AuthorizationCustody`, `AuditCustody` and canonical observation serialization
remain authoritative. Trusted constructor-only normalization/installed-worker and
archive hooks compose them: no wire-selected plugin, new grant engine, alternate
attempt ledger or source-system write path.

## Installed artifact and startup

The `orion-core` wheel contains the CLI and all runtime process entrypoints, not
tests or tools. Provision Python 3.12 and hatchling, then build/install offline:

```sh
python3.12 -m pip wheel --no-index --no-deps --no-build-isolation \
  --wheel-dir /operator/artifacts .
sha256sum /operator/artifacts/orion_core-0.1.0-py3-none-any.whl
python3.12 -m venv /operator/runtime
/operator/runtime/bin/python -m pip install --no-index --no-deps \
  /operator/artifacts/orion_core-0.1.0-py3-none-any.whl
/operator/runtime/bin/orion-runtime --artifact
/operator/runtime/bin/orion-runtime --health
```

The last command returns exit 2: release not ready. Neither it nor `--start`
activates access. `--artifact` validates installed RECORD hashes/sizes, including
the console entrypoint; integrity checking is not publisher-signature attestation.

For synthetic deployment only, the trusted unprivileged operator provides a
private manifest, existing canonical separately approved metadata/read configs,
private keys/state, separately retained witness storage, pinned TLS certificate,
installed RECORD digest and bounded archive policy. Mode must be
`synthetic_read_only`; host must be the fixed approved documentation IPv4 or
private IPv6 address. No arbitrary hostname/port, proxy, redirect, executable or
force-live selector exists. Before first startup, the operator explicitly enrolls
the witness once. Enrollment durably leaves a runtime-nonreplaceable receipt
outside the audit/evidence rollback subdirectories before creating witness
storage; normal startup, restart and repeated enrollment never create replacement
history. The witness service mounts that receipt read-only; application and
custody roles cannot write it.

Mutating candidate-host launch is narrower than the inspection console script.
Deployment profile version 3 binds the installed prefix/package root,
interpreter, isolated module, canonical private-manifest path, working directory
and environment policy. Enrollment and service start must use the exact commands
below from `/`; the generated `orion-runtime` console script is inspection-only
and rejects `--enroll-witness` and `--serve`.

```sh
cd /
env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  /operator/runtime/bin/python -I -m orion.pilot.deployment \
  --enroll-witness /operator/private/runtime-manifest.json
env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  /operator/runtime/bin/python -I -m orion.pilot.deployment \
  --serve /operator/private/runtime-manifest.json
```

See `WHEEL_ONLY_CANDIDATE_LAUNCH.md` for the exact service record, denial
boundary and trusted-host-administrator limitation.

Startup creates its own rootless user/network fabric, without PYTHONPATH, checkout,
tests, source fixture or host-network join. Bubblewrap, unshare/nsenter, ip, nft,
curl and working kernel namespaces are mandatory. Missing capability, invalid
private input, changed artifact/trust, tampered custody or pending reservations
denies startup. Real kernel baseline descriptors close before keys are loaded.

Initial output is `unarmed`: private source/control/gateway PIDs, artifact identity
and authenticated custody health. The synthetic operator places an ordinary HTTPS
service in that separate source namespace. The external source is a deployment
input, not a runtime test dependency; it understands only normal GET/Bearer and
ordinary JSON, with no ORION code, grants, receipts, IPC or custody. No route/NAT
to production or host network is created.

| Process | Network / selectively mounted custody |
| --- | --- |
| Trusted operator supervisor | Private control fabric; operator/issuer inputs and fresh role capabilities |
| Progress witness | Separate zero-network jail; witness key and writable monotonic deployment/scope/head positions only |
| Authorization/admission | Zero-network jail; issuer material, opaque normalizer secret, audit/archive IPC; no source credential |
| Audit custody | Separate zero-network jail; independent key, canonical writable journal and fsync accepted tip |
| Evidence custody | Separate zero-network jail; independent key, bounded archive/revision index and fsync accepted tip |
| Credential-use gateway | Private netns, one approved HTTPS tuple, capabilities zero; source credential and source-role authorization IPC only |
| Acquisition broker | Zero-network jail; requester/gateway capabilities, ordinary configs; no source credential, issuer/audit/archive keys or writable custody |
| One-shot reasoning | Separate zero-network jail; ordinary request and reasoner capability only; no control keys/storage |
| Ordinary synthetic source | Outside gateway netns; normal TLS key/credential/data, no ORION authorization protocol |

IPC is filesystem AF_UNIX, bounded JSON bytes and purpose-bound fresh HMAC
challenges with fixed role/action/scope enforcement, never pickle or TCP. Pinned
directory descriptors support long deployment paths without a network fallback.
Callers, handlers, frames and deadlines are bounded.

Only the confined gateway launches native curl, after online redemption of the
existing durable one-use reservation. Fixed numeric HTTPS destination/GET paths,
pinned TLS trust, bounded response and one-second source deadline are mandatory.
Credential travels on anonymous stdin, never argv/environment. No curlrc,
redirect following, caller headers or proxies; shared strict framing rejects
redirects, duplicate/missing/oversized lengths, coding and partial responses.
Native networking is explicitly declared, not hidden behind a scanner exemption.

Redemption is the source-use linearization point. Stop/revocation/expiry are
checked again before admission. An approved in-flight GET cannot be retrospectively
unauthorized; emergency kernel cutoff/termination removes further I/O and prevents
post-stop admission. No retries, arbitrary relays or source writes exist.

## Persistence, recovery and operational behavior

`EvidenceCustody` retains canonical admitted observations/checkpoints with exact
tenant/company/source/grant binding, journal/request references and cumulative
per-record revisions. The historical batch deliberately rejects admitted
provenance payloads; the necessary admission-specific index reuses serialization
and canonical re-admission instead of weakening that contract. Persistence and
its authenticated private tip finish before admitted output. Custody, capacity and
accepted-request replay checks precede reservation/redemption, including after
restart and content expiry.

Policy bounds lifetime checkpoints (maximum 1,000), retained bytes (maximum
20 MiB) and payload TTL (maximum 24 hours). Full capacity denies acquisition, never
resetting/refunding history. Retention runs on append/load/health and supervision.
SQLite secure deletion removes expired payloads; authenticated provenance/revision
references and tombstones remain. Missing content is UNKNOWN. This is logical
expiry, not forensic media, backup or export erasure proof.

Private stdin/stdout supports bounded health, arm, read, restart, revoke and stop.
Canonical nonce/head authentication governs controls. Health exposes budgets,
discovery this start, process count and archive availability/bytes. `healthy` means
custody health, not live readiness. Source status remains UNKNOWN: an unauthorized
health GET/TCP probe is not substituted for governed access.

A separate bounded monitor continues during blocking reads. Child death,
custody/index failure and expiry drive actual private nft allow-rule removal
(default DROP remains) and gateway/acquisition termination. Poll interval is
0.5 seconds; IPC deadlines are eight seconds, not a certified subsecond SLO.
Emergency stop removes egress first, then attempts durable controls for both
journals. Failed nft commands or missing acknowledgements report BLOCKED without
skipping termination/controls or claiming successful kernel/durability proof.

Restart revalidates the artifact, deployment profile, artifact binding, secret
layout and separately retained witness before reconstructing protected
journal/archive owners. Attempts, bytes, stop/revocation and revisions remain.
Armed authority and discovery completion never restore. Witness disagreement or
tip/database/config/key/scope/payload mismatch denies recovery; orphaned pending
reservations are not repaired, retried or refunded.

## Exact-artifact evidence, trust and remaining gates

`tests/test_packaged_runtime.py` builds/installs the actual wheel offline into a
clean environment and drives the packaged CLI against an external unmodified
source. This is verification infrastructure, not another runtime or startup
dependency. Actual WSL scenarios exercise discovery, separate reads, admission,
archive/revisions, restart/replay/budgets, retention, revocation, spontaneous
audit/authorization/evidence loss and emergency stop. Controls positively reach
approved and unapproved targets. Raw capability-zero sockets bypass URL validation
and must increment named nft counters for enabled IPv4/IPv6, mapped IPv4, other
family, alternate ports and proxies. All existing probes remain required.

Trusted: reviewed kernel/host/setup and operator, private key custody,
authorization/admission and its sealed normalizer, gateway/audit/archive owners,
interpreter/native tools, TLS/source-body/classification truth, clock and verifier.
The normalizer's opaque seal is not the source credential. A hostile privileged
host/custody owner is not contained by HMAC. The separately retained progress
witness detects rollback of an audit/evidence database together with its protected
accepted tip while the witness database, key and deployment binding remain intact.
It does not prove whole-host or VM/filesystem-snapshot rollback protection; see
`ROLLBACK_WITNESS_CONTRACT.md`.

All 19 release categories stay critical. SECURITY/SECRETS remain FAIL for legacy
same-process APIs. Others stay BLOCKED for independent issuer/operator/IPC identity
and artifact review; production discovery/ERP/pagination/protocol lifecycle;
semantics/world model; collector-root/archive/recovery attestation; tenant legacy
storage; audit/failure lifecycle; alert delivery/SLOs; sensitive retention/erasure
policy; production OS/mount/key policy; full release simulation and epistemic
promotion review. Reasons distinguish implemented synthetic boundaries from
unattested production requirements. Test totals never satisfy gates by inference.

## Operator procedure for eventual controlled activation

Now: do not activate or supply customer credentials. Retain exact SHA, wheel hash,
RECORD and executable reports. Use only the synthetic private manifest. Arm
independently granted metadata, admit provenance, then separately approve/arm the
exact record grant and limits. Discovery cannot manufacture record permission;
output is observations plus explicit UNKNOWN.

Incident: private `stop`/`revoke` or SIGTERM; retain both durable acknowledgements
and kernel/termination results. Missing witnesses are BLOCKED. Preserve history,
restart unarmed with retained budgets, and independently reconcile pending/tampered
state; never delete/reset it to regain authority.

Eventual customer activation needs a separate access ledger, independently
reviewed production artifact/host/key/identity policy, source lifecycle validation
and satisfaction of every critical gate. Human maintainers alone may authorize it.
Readiness never authorizes customer access, writes, merge or activation. There is
no available live activation override or customer activation procedure in this tree.

## Installed application compromise boundary (issue #165)

PR #164's installed wheel reproduced two concrete failures: reasoning stdout
could claim admission with empty self-reported checks, and custody placed beneath
the interpreter prefix was readable through ambient read-only mounts. Startup now
rejects keys/state overlapping any exposed interpreter/system tree, in either
direction, including symlink traversal. The same mount-root owner constructs the
jails and validates placement, both at manifest loading and supervisor construction.

The protected admission checkpoint optionally authenticates the exact original
request-message digest in addition to canonical caller/request references. Pilot
admission always supplies it. The trusted supervisor rejects prior accepted
request references before reasoning, then independently resolves the exact retained
checkpoint through owner-authenticated EvidenceCustody. Scope, original message,
checkpoint/payload pins, journal head and canonical observations must match.
Custody loss, expired content, fabricated admission, scope/token substitution and
replayed output deny. Legacy checkpoints without that binding cannot prove pilot
output. Returned output is reconstructed from protected observations and fixed
UNKNOWN/read-only fields; arbitrary application-supplied fields are discarded.
Self-reported namespace checks are diagnostics, not an admission attestation.

`tests/installed_runtime_attacker.py` is an external verifier, not shipped runtime
code or a public plugin. It captures installed production jail commands and changes
only their executable suffix to model arbitrary application compromise. Live source
and custody remain the actual integrated deployment. It also replaces acquisition's
own disposable socket with a forged response: the unchanged reasoning process and
independent supervisor deny publication. The ordinary deployment scenarios retain
the installed public CLI.

Legacy adapters, credential mappings, raw gateway constructors, Python native
network libraries and alternate installed modules remain callable/importable. They
are not claimed unreachable or safe in an unconfined host process. In the pilot
application roles, malicious fetcher/opener/mapping/gateway callbacks execute but
cannot read real custody, reach source networking or issue independently accepted
authority. Direct sockets, curl/proxies, namespace entry and nft mutation are
tested at the OS boundary, with externally reachable positive controls and no source
I/O or protected-history mutation. Fabricating one's own secret/grant is not access
to the real issuer or source credential. Imported methods alone are not containment.

Trusted host/operator, interpreter/native tools, kernel and custody services remain
outside this application-compromise claim. The retained witness closes only the
specified custody-directory rollback case; whole-host rollback, compromised
trusted custody, production key/operator identity and OS CPU/memory quotas are not
proven. Request/byte/rate budgets are not CPU quotas.
SECURITY and SECRETS therefore retain their broader repository FAIL requirements;
all other seventeen critical release gates remain BLOCKED as enumerated in the
unchanged executable gate report. Synthetic installed-boundary proof does not
certify legacy same-process APIs or production readiness. LIVE_PILOT_READY=false.
