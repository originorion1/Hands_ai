# Retained rollback witness contract

## Scope and claim

The installed supervised runtime retains one authenticated monotonic progress
witness outside the custody rollback directories. While that witness database,
its signing key and the deployment binding remain intact, restoring an older
custody database together with its locally accepted tip is rejected before a
source acquisition or semantic publication can succeed.

This is deliberately a narrow same-host claim. The implementation does **not**
protect against rollback of the whole host, a VM or filesystem snapshot that
also restores the witness, privileged removal or replacement of the enrollment
receipt together with the witness database and key, or rollback of independently
retained backups. A
qualification host must supply genuinely independent retention if those threats
are in scope. `LIVE_PILOT_READY=false` and `execution_allowed=false` remain
unchanged.

## Exact rollback and witness sets

The rollback set is the deployment's configured state directory:

- `audit/**`: each operation's `broker.db` and `accepted-head`, including
  attempt, failure, pending, stop, lifecycle and budget history;
- `evidence/**`: `evidence.db` and `accepted-evidence-head`, including retained
  observation indexes, expiry tombstones and semantic checkpoint records.

The witness set is configured separately:

- `<state_directory>/witness-enrollment` is a durable, runtime-nonreplaceable
  enrollment receipt outside the `audit/**` and `evidence/**` rollback
  subdirectories;
- `<witness_directory>/progress-witness.db` stores authenticated deployment,
  stream, sequence and chain-head metadata;
- `<keys_directory>/witness-signing` authenticates that metadata;
- the dedicated witness service alone mounts witness storage read-write and
  receives its signing key; it receives the enrollment receipt read-only.

The deployment validator rejects overlapping state, key and witness roots. The
application and custody roles cannot write the enrollment receipt or witness
storage, read the witness key, or invoke witness owner/advance operations with
their capabilities. The witness
stores digests and monotonic positions, not source credentials, evidence
payloads, grants or authority.

## Binding and enrollment

One witness contract is bound to:

- contract and manifest versions;
- installed package name/version and installed `RECORD` digest;
- deployment profile digest and stable deployment identity;
- absolute state, key and witness roots and the configured host;
- the exact audit scope digests and the combined evidence scope digest;
- the semantic policy digest when semantic recovery is configured.

Enrollment is an explicit, one-time operator action:

```text
python -I -m orion.pilot.deployment --enroll-witness PRIVATE_MANIFEST
```

Enrollment verifies or initializes the existing custody stores, derives every
current sequence/head pair, durably creates the enrollment receipt with exclusive
creation, and only then creates the witness database. The receipt is never removed
on partial enrollment failure. Normal startup and restart require its exact
deployment/contract binding and never enroll, reset or infer replacement witness
state. An existing receipt or database cannot be enrolled again, so deleting the
witness database does not enable bootstrap against rolled-back custody. Missing,
malformed, unauthenticated, substituted or differently bound witness state
causes startup or restart to use the existing cutoff behavior.

The explicit enrollment action is bootstrap, not a recovery procedure. After
loss of an enrolled witness, recreating trust requires a separately reviewed
recovery contract; this implementation supplies none.

## Runtime protocol and ordering

Audit custody has one stream per configured acquisition scope. Evidence custody
has one stream covering its fixed scope set. Only the audit owner may operate an
audit stream and only the evidence owner may operate the evidence stream.

Before use, custody sends its current stream identity, kind, scope digest,
sequence and chain head to `verify`. A mismatch is a rollback/disagreement and
fails closed. For a mutation, the order is:

1. verify the current local position against the witness;
2. commit the authenticated local database transaction;
3. durably replace and fsync the local accepted-tip file;
4. conditionally advance the witness from the exact predecessor;
5. return the custody result or allow semantic publication.

The witness serializes competing writers and accepts only a strictly newer
position from the exact retained predecessor. Repeating the exact accepted
transition is idempotent; stale, reordered, conflicting, wrong-scope and
wrong-role requests are rejected.

Crash or response uncertainty is fail-closed:

| Boundary | Durable state after interruption | Result |
|---|---|---|
| Before local commit | Old local state and old witness | No mutation or success. |
| After local commit, before local pin | Local database is ahead of its tip | Existing local integrity check blocks restart. |
| After local pin, before witness acceptance | Local custody is ahead of witness | Witness verification blocks restart and publication. |
| After witness acceptance, before response | Local custody and witness agree | The RPC reports failure/uncertainty; an exact witness-transition retry is idempotent. A begun acquisition remains pending and is not refunded. |

Retention advances the evidence stream after durable expiry events; erasing
payloads cannot lower or erase the witness high-water mark. Durable stop and
consumed budget state are part of the witnessed audit chain. Restart remains
unarmed and does not restore authority.

## Pending acquisition limitation

The existing cutoff-first lifecycle intentionally has no recovery contract for
a pending acquisition. If witness response is lost after an audit `begin`, the
accepted attempt remains pending and consumes its durable budget. Restart and
new acquisition remain blocked; an operator signature cannot resolve that
technical state. This limitation remains relevant to any proposed pilot and
must be accepted or closed by a separately scoped recovery design.

## Verification surfaces

Deterministic unit coverage exercises explicit enrollment, missing/corrupt and
substituted state, binding and role rejection, conditional/idempotent advances,
competing writers, audit and evidence rollback, all local/pin/witness acquisition
boundaries, semantic publication uncertainty, pending-budget preservation and
retention high-water behavior.

Installed-runtime cases use only synthetic sources and credentials and exercise:

- rollback of an audit database plus accepted tip, including stopped state;
- rollback of the evidence database, accepted tip and semantic records;
- denial before source I/O, rearm, budget reset or stale world-model publication;
- witness process outage and intact unarmed restart;
- missing and corrupted witness state on fresh startup;
- mount, namespace, capability, key, storage, IPC and network-counter isolation.

Those results qualify this artifact only for the explicit retained-witness
threat model. They must not be reported as whole-host rollback protection,
customer-system access, activation, release-gate promotion or approval.
