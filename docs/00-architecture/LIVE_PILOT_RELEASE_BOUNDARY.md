# Controlled pilot release boundary

## Verdict and audited state

**NOT_LIVE_PILOT_READY.** This tree must not touch a real organization.

The source inventory is retained as a historical snapshot bound to its declared
`dependency_head` (`2375ad61af37f8e13a6c59935051d614e2ead8cb`). It does not
establish complete inventory or review coverage for this restacked tree, including
the subsequent historical-capture hardening and #195 storage-lifecycle
correction. The accompanying `LIVE_PILOT_GAP_MATRIX.json`
maps 34 architectural areas at the audited snapshot to implementation,
evidence, risk and missing work; it is not an independent
full-code penetration test or a current-tree certification.

The historical audited stack was PR130 above PR128 above laboratory head
40c02b3. Historical window PR124 is already a dependency of PR128.
Prediction/outcome code is in separate unmerged PR119/121; workers in
PR117/121; Claude handoff in PR114. None
was merged or silently treated as deployed functionality. Semantic commercial
validation is not proven, so prediction integration is deliberately deferred
rather than fabricated or duplicated.

Two concrete escapes remain: directly injecting a fetcher into the legacy HTTP
adapter performs I/O without a pilot grant, and prototype validation can accept a
caller-supplied evidence count without independent attestation. These are explicitly
reproduced as release failures, not presented as successful security defenses.
Legacy unscoped stores and ERP-specific business profiles must also be excluded
from any eventual generic live boundary.

## Implemented increment

- `pilot.journal`: durable, grant-digest-bound request reservations, request/response
  byte ceilings, aggregate reserved-byte budget, single active attempt, rate
  spacing, failure circuit latch and explicit stop. A pending attempt after a
  crash blocks continuation; failed requests retain their budget cost.
- `pilot.budgeted_transport`: composes those limits with `open_pilot_read` before
  dispatch and after response. Both pilot ERP record and metadata readers accept
  a journal, and tests exercise it through actual pilot launchers. No raw network
  opener was added. Retries are disabled; another attempt requires remaining
  budget, elapsed rate interval and freshly valid authorization.
- `understanding.process_checkpoint`: reference-only recovery of both base study
  and independent process traces, with pinned digest/protocol and revalidated
  evidence/request fingerprints. New-process tests reconstruct supported or
  contradicted claims and deny revoked continuation before adapter invocation.
- `pilot.readiness`: bounded strict JSON configuration and an executable report
  with all 19 critical release categories. Unknown/duplicate fields, secret
  values instead of references, write mode and activation overrides are rejected.
  The startup function rejects operation while any gate fails or is blocked.

Journal success means **transport completion**, not evidence admission, semantic
validation or execution success. Full lifecycle/authorization/epistemic auditing
is still missing. The HMAC key and latest head must be retained by a separately
trusted owner. The ledger can detect alteration and rollback against that head;
it cannot establish independent custody or defeat hostile code holding its key.
Journal injection is optional laboratory composition, not mandatory OS mediation.

## Machine-readable gate and startup

Run from an installed reviewed tree:

```sh
python -m orion.pilot.readiness
python -m orion.pilot.readiness --config /path/to/private/pilot.json --start
```

Both currently return exit code **2**, `live_ready=false`. The first emits a
non-sensitive gate report. The second validates configuration and denies startup;
it does not resolve secrets, instantiate a network worker or access an endpoint.
There is no force flag, environment-selected callable or automatic activation.
These commands are exercised in tests, not decorative deployment files.

Configuration has exactly these keys: `tenant_id`, `company`, `source_id`,
`key_reference`, `secret_reference`, `mode`. The source must be an exact HTTPS
origin; credential entries are environment-variable **names**, and mode must be
`read_only`. Configuration establishes neither grants nor authority. Do not
supply customer values or credentials during development. There is intentionally
no customer-filled example and no real activation procedure in this release.

## Required deployment separation

The eventual worker must have no credential, grant-issuer, audit-key/index or
unrestricted network access. A separately authenticated broker must own grant
issuance/revocation, destination and wire allowlists, secret injection, TLS,
request budgets and durable auditing. The only worker egress must be that broker;
files, inherited descriptors and arbitrary subprocesses must not provide an
escape. The issuer/control plane must authenticate human operators independently
of the reasoning process.

This environment has `bwrap`, but its network-namespace probe fails with
`Failed to create NETLINK_ROUTE socket: Operation not permitted`. Consequently
no claim of tested namespace isolation is made. No untested Docker/Kubernetes
configuration or alternate raw opener is introduced to disguise this blocker.
A reviewed host and a tested external security boundary are required before live
readiness can change. A fresh Python process is a restart test, not a sandbox.

## Data, secrets and audit handling

New journal entries contain only grant digest, bounded counters, fixed event
categories, timing and integrity links; not URLs, headers, record contents or
credential values. Journal files require private owned directories and regular
owner-only files; symlinks/hardlinks are rejected at construction. This is not a
complete protection against a hostile process that can replace filesystem paths.
Checkpoint files contain references/hashes, not records, grants or credentials.
The independently owned evidence archive must remain available after restart.

Existing name/type screening is not a complete financial/personnel/payment/PII
classification policy. Retention, erasure, evidence invalidation after retention,
authenticated audit export and secret-free alerting remain release blockers.
No raw production data retention policy is inferred from grant expiry.

## Operator activation and emergency stop

**Current activation procedure: do not activate.** Run the gate command, retain
the report and leave the process stopped. The gate does not currently offer a
path to readiness; missing security components require reviewed implementation,
not configuration acknowledgements.

For the eventual deployment, the operator must verify the exact release artifact,
all critical gates, isolation, trusted index/key custody and independently issued
metadata-only grant. Record access needs a separate exact reviewed grant after
discovery; metadata confidence never creates one. Only then may a reviewed
supervisor launch a bounded worker. That supervisor is not implemented here.

For the implemented laboratory journal, `AttemptJournal.stop()` durably prevents
further mediated attempts and blocks post-response acceptance. Revoke the grant
in its authoritative lookup as well. A pending/crashed attempt is not silently
retried or refunded. Recover only with the separately retained correct audit tip.
This API is not an authenticated global emergency control plane. Real deployment
must additionally remove broker egress and terminate the supervised worker;
those mechanisms must be implemented and tested before activation is allowed.

## Deliberate limits

No customer connection, credentials, production activation, merge, raw network
transport, OS isolation certification, autonomous permission, write capability,
complete semantic-business classification, prediction deployment, consequential
recommendation execution or whole-lifecycle audit is supplied by this change.
The code improves offline enforcement and recovery while making the unresolved
live boundary explicit. Passing its tests does not turn these omissions into
capabilities.
