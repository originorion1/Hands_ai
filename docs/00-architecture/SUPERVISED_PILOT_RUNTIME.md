# One supervised read-only pilot runtime

Issue #161, based on PR #160 head `7b86e6366159911352f0def5c6253016b69f679d`.
This is one integrated runtime candidate with an executable synthetic acceptance
deployment, **not a customer-ready release or activation authorization**.
`LIVE_PILOT_READY=false`; all existing release gates remain unchanged.

## Minimum acceptance and scope

Essential safety requirements for this bounded runtime are governed metadata
discovery, an independently issued exact record grant, mandatory kernel egress
confinement, independent credential/grant/audit custody, tenant/company/source
binding, canonical admission/provenance, bounded requests and durable limits,
expiry/revocation/stop, restart unarmed, and fail-closed health/startup.

Prediction, forecasting, autonomous actions, additional commercial roles,
multi-source product features and richer business interpretation are not needed
to exercise one read-only acquisition. This distinction **does not** make any
existing critical release gate optional, NOT_APPLICABLE or passed. Two synthetic
record encodings remain two local encodings, not two verified ERP protocols.

## One state owner, existing semantic contracts

`pilot.runtime.SupervisedReadOnlyRuntime` composes the existing metadata and record
supervisors. Grants, requests, admission, observations, classifications and
provenance retain their canonical contracts. A new runtime starts with neither
discovery completion nor armed record authority. Canonically admitted metadata
must complete this start before record arming/acquisition; discovery itself never
issues a record grant. Metadata and records have separately bound journals and
budgets. Acquisition produces observations and explicit UNKNOWN commercial
interpretation, never prediction, permission, knowledge promotion or execution.

`pilot.custody.AuthorizationCustody` retains the existing Broker as authorization
and admission supervisor **outside** the acquisition broker. The fixed sealed
worker still normalizes the exact pinned response. The only Broker constructor
seam is a trusted Python `journal_factory`, never selectable by configuration or
application messages. Stop is now durable before its lifecycle event, permitting
independent denial of a pending source redemption.

`AuditCustody` is the single writer of canonical `AttemptJournal` history. It has
an independent audit key, verifies fixed binding/caller references and permits
only the existing journal operations for its authenticated supervisor role. The
latest acknowledged tip is fsync-pinned in its private custody directory before
any successful RPC acknowledgement. This tip is an anchor, not a second history
store. A crash between ledger commit and anchor commit blocks restart; it does
not silently repair, reset, refund or reconstruct accepted history.

`RuntimeCustody` exposes one integrated endpoint with broker/source/owner roles.
The acquisition process can request only bounded begin/complete operations using
a requester capability. It cannot sign grants, widen scope, impersonate the
source/operator, or invoke audit operations. Fresh RPC challenges bind requests
and responses; they confer no grant-issuer signing authority.

## Actual synthetic deployment path

Run from the reviewed checkout and its development environment:

```sh
PYTHONPATH=src:tools python3 tools/pilot_runtime.py --verify-synthetic
PYTHONPATH=src:tools python3 tools/pilot_runtime.py --health
PYTHONPATH=src:tools python3 tools/pilot_runtime.py --start
PYTHONPATH=src python3 -m orion.pilot.readiness
```

Only the first command exercises disposable synthetic infrastructure. The other
commands return exit 2 and retain startup/readiness denial. There is no force
flag, endpoint/proxy selector, live transport, customer configuration, or gate
override. Verification requires bubblewrap, unshare/nsenter, ip, nft, openssl and
working WSL kernel namespace capabilities. Missing capabilities report BLOCKED,
not a substituted application check or unreachable destination.

The acceptance deployment mounts the runtime package read-only into independently
isolated processes and reuses the existing TLS/fabric/namespace mechanisms:

| Process | Network / custody |
| --- | --- |
| Authorization/admission runtime | Zero-network jail; issuer key and source credential; authenticated audit IPC only |
| Audit custody | Separate zero-network jail; independent key, writable journal and private tip |
| Acquisition broker | PR #160 private netns, exactly one HTTPS tuple; requester capability/one-use receipt; no issuer/audit/source keys, source files or writable history |
| Synthetic source | Outside broker netns; TLS key; source-role IPC capability; independently redeems before fixture file I/O |
| Reasoning consumer | Existing separate zero-network jail; ordinary request and admitted observations only; no control keys/storage |

No process joins host networking. No control helper performs HTTP, proxies,
relays or unrestricted networking. The source issues no data on forged, replayed,
stopped or unavailable-custody receipts. Redemption is the authorization
linearization point; a later stop also prevents post-response admission. There
is at most one outstanding operation, with bounded deadlines and no retries.

Approved and unapproved listeners are positively reachable from the fabric
control environment. Direct broker sockets bypass application validation and
must increment matching nft rejection counters for IPv4/IPv6 where enabled,
alternate ports, proxy tuples and mapped addresses. Existing PR #160 redirect
and all other adversarial checks remain required; none is replaced here.

Five deployment scenarios cover metadata-first record acquisition, both local
encodings/IPv6, restart of both custodians, budget continuity, replay, revocation,
durable emergency stop, loss of either custodian with a valid unused receipt,
and stop before source redemption. The emergency procedure also removes the
only accepted route in the broker's private namespace and terminates acquisition.
The public validator rejects incomplete witnesses rather than trusting a PASS
label. Focused tests cover canonical scope, expiry, RPC replay, unauthorized
roles, protected-tip corruption/rollback and source-side denial.

## Proven, trusted, blocked

Verification evidence is reported against the exact published tree in the draft
PR. WSL acceptance demonstrates the synthetic deployment boundaries, not a
production identity provider, immutable-host guarantee or isolation certification.

Trusted: reviewed host/kernel/setup, authorization and audit custody processes,
private role capabilities and operator/issuer keys, pinned source fixtures and
classification truth, clock, canonical admission/semantic implementations, and
the verifier. Compromise of custody or its trusted OS owner is not contained by
an HMAC. Source receipts require the independent source participation exercised
here; an ordinary ERP does not implement this synthetic source-side protocol.

Blocked: reviewed production packaging/service lifecycle and IPC identity,
operator identity/approval ledger, production ERP credential-use mediation and
metadata/pagination lifecycle, protected admission/archive/checkpoint index,
sensitivity/retention/erasure/export policy, production monitoring/alerts, and
independent exact-artifact security/release review. The current installed wheel
does not include the synthetic source/test fixtures. The verified acceptance
deployment is a reviewed-checkout path, not a claim that the wheel alone deploys
a live runtime. Production source capability policy still forbids raw openers;
this increment neither weakens that scanner nor creates an unconfined opener.

## Operator procedure for eventual controlled activation

Current procedure: **do not activate**. Retain the exact SHA and executable gate
reports; use only the synthetic verification command. Never supply customer
credentials to this development path, infer approval from test totals, or reset
audit state to regain budget.

Eventual activation requires a separately reviewed production deployment artifact,
independent satisfaction of every existing release gate, host/mount/netns/key/tip
validation, production source mediation, operator identity and explicit access
ledger. Start custody first and deny if either is unavailable, stale or pending.
Start acquisition and reasoning unarmed. Independently authorize metadata-only
discovery, verify admitted provenance, then independently approve exact technical
record scope and bounded limits. Fresh nonce/head controls arm that grant only;
metadata interpretation must never manufacture the grant. No business mapping or
schema names are requested from the user by this implementation.

On incident: authenticate stop/revocation at custody; retain its durable audit
acknowledgement, remove acquisition egress at the private kernel boundary and
terminate acquisition/reasoning. Restart never restores authority or refunds
attempts. A pending or mismatched tip requires independent reconciliation and
review, not automatic retry or a new empty ledger. These are conditional future
requirements, **not an available live activation procedure in this release**.
