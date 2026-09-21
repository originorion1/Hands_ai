# Locally supervised read-only broker

Issue #137, stacked on PR #136 head `8dc0c0cb8a72bde2907c547b25b7297aa7f027ec`.
This is an executable offline process boundary, **not a live runtime or OS sandbox**.
The existing release report and startup denial remain unchanged.

## Composition and custody

`orion.pilot.broker` is a supervisor intended to run outside the ORION application.
It accepts bounded newline-delimited JSON on stdin and emits bounded JSON replies.
The fixed child `broker_worker` is launched with an empty credential environment,
closed inherited descriptors except a one-use seal pipe, isolated Python import
mode, a pinned repository module path, and a five-second deadline. It cannot be
selected or replaced by an application message. The worker receives its source
credential only in an anonymous supervisor pipe; it receives no issuer key.
A separate seal descriptor authenticates the child bootstrap before source access.

The supervisor resolves two approved environment **references** from owner-only
configuration: an authentication/journal key and a synthetic source credential.
The application wire format has no credential, reference, path, URL, module or
callable parameter. Extra keys reject. A read token is an HMAC capability bound
to the complete configuration digest, including the canonical grant, caller,
source-file digest, encoding, references and limits. The token is reusable within
that grant's bounds; individual request IDs are single-use, including failed
reserved attempts. It is not a caller identity provider or human approval service.

The supervisor reuses `PilotAuthorization`, `PilotRequest`, `launch_pilot_read`
and canonical admission. It reserves the existing `AttemptJournal` before spawning
a source worker. The child also uses the canonical launcher, then the supervisor
reauthorizes and admits the returned batch. No new canonical evidence or hypothesis
type is introduced. Returned observations can enter the existing RoleStudy and
SemanticStudy archive without semantic mapping changes.

## Actual source boundary

Only the explicitly selected `local_rows_v1` and `local_columns_v1` formats exist:
one carries JSON row objects, the other an ordered column array plus value tuples.
These are two synthetic wire formats over local files, **not two live ERP protocols**.
Both require exact resource and field bindings and a synthetic credential digest.
The source is an owner-only regular file, opened without following a final symlink,
read with a 64-KiB ceiling and checked against the supervisor's pinned SHA-256.
FIFO/device input rejects. Only `.test` HTTPS-shaped source identities are accepted;
they are provenance identifiers and are never contacted. There is no HTTP client,
URL forwarding, production adapter, write method or dynamic adapter registry.

Resource mechanics and field identifiers are supplied by the independent synthetic
control-plane fixtures, not inferred as authority by semantic reasoning. The
integration tests retain existing separately admitted metadata, then route record
and independent process evidence through the broker. Metadata transport itself
has not been moved behind this broker and remains a future reviewed increment.

## Supervisor contract

The trusted owner starts the process with `--config`, `--state`, and, for restart,
the independently retained `--expected-head`. These arguments are **not** accepted
through the application protocol. Every start is unarmed. A fresh random challenge
and current journal head must be authenticated with a domain-separated control MAC
before arming. No active authority is restored from disk. Only `arm`, `stop` and
`revoke` are recognized controls. Stop and revocation are terminal for this grant
instance and retained in the journal. Restart cannot undo either.

Controls and requests are serialized. A stop/revoke acknowledgement establishes
the stop; sending a control message does not imply it has already been applied.
A graceful control can wait for the bounded current worker operation (five seconds).
Immediate host-level kill/revocation, protected process groups and asynchronous
source revocation remain deployment work. The worker has its own alarm if orphaned;
this is not proof against an uninterruptible kernel/filesystem operation.

A pending attempt after supervisor termination denies restart. Budget reservations
are not refunded. Rate spacing and the failure circuit survive restart. Missing,
stale or altered audit tips, changed configuration, and modified journal entries
reject. The supervisor does not reconstruct or manufacture a lost trusted tip.

## Audit and minimization

Broker lifecycle events use the existing journal HMAC chain and its fixed 202-event
limit. The complete provisioned record lifecycle reserves configure and transition,
start and arm, four events for each completed request, one expected over-budget
request and denial, and terminal `stop` and `broker_stop`. That shape supports at
most 48 supervised requests. A supervised configuration or post-discovery envelope
requesting 49 through 100 is rejected before credential resolution, journal creation,
witness advancement or source access; it is never clamped to a smaller budget.
Existing supervised journals at 48 or fewer requests require no migration. The
legacy direct `AttemptJournal` transport remains compatible with 100 requests
because its configure, two-events-per-request and stop shape fits exactly.

Events cannot be appended over an unfinished attempt. There is no second persistence
system or unbounded audit growth. Additional denials and restarts still consume the
bounded lifecycle journal, and exhaustion fails closed explicitly.

Events retain timestamps, the configuration/grant binding, hashed caller, request,
version and observation-set identities. Denials retain a hash of the fixed stage:
`request_validation`, `control_authentication`, `grant_authentication`,
`scope_authorization`, `resource_reservation`, `worker_acquisition`, or `admission`.
The external retained configuration and observation archive resolve those bounded
references; the journal is not a standalone copy of all evidence. Transport success
does not claim admission success; `broker_admitted` is recorded after admission.
No source rows, credential values, paths, headers or exception text are journaled.
Literal source/issuer credential values appearing in output are rejected. This is
not a complete classifier for transformed secrets or sensitive business data.

Both journal and configuration remain accessible to their OS owner. A same-UID
hostile process may read files or `/proc/<pid>/environ`, replace code, invoke legacy
adapters or create another state directory. Neither Python privacy nor MACs held
in the same trust domain prevent that. Mandatory protected custody, anti-reset
index ownership, filesystem isolation and kernel egress controls remain required.

## Executable checks

`tests/test_supervised_broker.py` starts actual supervisor and worker processes,
tests both formats, rejected application/control frames, scope and MAC attacks,
replay, limits, fresh arming, stop/revocation recovery, changed audit/configuration,
source failures, terminated supervisor with a pending attempt, credential leakage,
and existing semantic contradiction-review/checkpoint integration.

The existing `test_pilot_release_adversarial.py` still demonstrates the injected
legacy-adapter bypass. The same-UID probe is diagnostic, not a passing isolation
assertion. No tests or scanner policies are weakened to make this increment pass.

## Release assessment

| Requirement | Status | Limit |
| --- | --- | --- |
| Supervised local read broker | PASS | Fixed synthetic child only |
| Authenticated scoped read/control protocol | PASS | Trusted issuer and bearer custody required |
| Budget/rate/replay/stop recovery | PASS | Independently pinned audit head required |
| Local record formats through semantic review | PASS | Metadata remains on its existing separate path |
| Mandatory broker for all application paths | FAIL | Legacy injected calls still exist |
| Process credential/API minimization | PASS | Does not establish OS custody |
| Protected secret/journal custody | BLOCKED | Same-UID access is not contained |
| Production egress/OS isolation | BLOCKED | Network-namespace probe fails in this environment |
| Full second live protocol lifecycle | BLOCKED | Two local encodings are not live protocols |
| Monitoring/retention/deployment validation | BLOCKED | No deployed supervisor or protected storage |
| Live customer readiness | BLOCKED | Existing release gates unchanged |
| Execution authority denied | PASS | No action interface or live activation |

The next boundary is an independently owned, kernel-enforced broker/application
deployment on a host where isolation can actually be tested. It must prove that
the application cannot reach broker keys, source files, process environment or
alternative network paths. Do not activate this local laboratory service for a
customer or reinterpret its test results as that deployment proof.

## Field classification boundary (issue #143)

`local-broker-v2` requires `field_classifications`, an exact mapping from every
field in the canonical grant window to `public`. This is an independently supplied
control-plane classification, not inferred from names, descriptions, model output
or confidence. No other class is supported by this first local profile. Hidden,
sensitive, personal, authentication and unclassified fields reject; there is no
application waiver. The planner's existing public-only policy is preserved.

The complete mapping is covered by the existing configuration digest, grant MAC,
journal binding and sealed worker bootstrap. The supervisor validates it before
resolving credentials and again before dispatch; the worker validates before
source access. Application messages cannot supply classifications. Configuration
mutation denies continuation. Old v1 configurations/tokens/journals must not be
silently migrated or reset: preserve their audit tips and use an independently
reviewed new configuration/authorization and explicitly reconciled budget state.
No migration or authority restoration is supplied by this increment.

Both local encodings use this contract. Column envelopes must match the requested
field set even when no rows are returned. This does not supply a second metadata
or pagination protocol. Classification is not content inspection: a dishonest
trusted owner can mislabel sensitive values public, and transformed credentials
are not comprehensively detected. This limitation remains a live blocker.

`PYTHONPATH=src python3 tools/read_only_boundary_gate.py` executes the full suite,
lint, compile, scan, demo and diff checks, then reports 25 boundary criteria.
Missing/skipped test witnesses do not pass. The report is trusted-verifier evidence
accounting, not signed attestation, a grant or a startup override. Known containment,
metadata, protocol and custody gaps remain FAIL/BLOCKED even with a green suite.
Exit 2 and READ_ONLY_PILOT_BOUNDARY_NOT_READY are the expected current verdict.
The independent live startup gate remains unchanged and denied.


## Brokered metadata discovery (issue #149)

The current wire/configuration version is `local-broker-v3`. Configuration requires
an explicit `operation` (`read` or `metadata`). That operation, protocol, source,
grant, limits and secret references are covered by the existing MAC/journal
binding. Metadata and record requests/grants are parsed as distinct canonical
types. A metadata token cannot authorize records, even with the same source.
Old configurations fail closed; this version supplies no audit/budget migration.

Metadata uses `MetadataAuthorization`, `MetadataRequest`, the shared canonical
authorization guard, and `launch_pilot_metadata`. Its grant explicitly permits
site schema discovery for a tenant/company/source, with exclusions, expiry and
bounded catalogue/schema counts. The broker profile caps catalogue visibility at
10 and schema acquisitions at 2. Metadata can expose site resource names; company
binding is context, not a claim that a site's schema belongs exclusively to one
company. Record scope still requires an independently supplied record grant.

Only the fixed synthetic `local_schema_v1` file format is supported. The trusted
owner pins its digest and supplies source credentials to sealed workers, never
the consumer. Exact schema-only envelopes reject records, scripts and defaults.
Each field has a classification; only public structural declarations are emitted.
Missing labels reject, other classes are withheld, duplicate fields reject. A
public string that resembles an instruction remains an opaque identifier. Types
produce candidates/UNKNOWN, never validated business roles or authorization.
Classification accuracy remains a trusted collector responsibility.

The supervisor launches one sealed fixed worker for the catalogue and one for
each selected schema. Each file acquisition consumes its own durable journal
attempt and response-byte reservation. Existing rate limits remain enforced;
bounded scheduling waits for the next allowed attempt within a five-second
operation deadline, without retry. Worker timeout is limited by the remaining
deadline. Denial/partial failure returns no metadata observations. Canonical guard
checks run before dispatch and after response, including configuration integrity,
expiry and durable stop/revocation. Workers recheck canonical scope before file
access. Supervisor reconstructs structural interpretations and canonical metadata
admission produces immutable observations with authorization/scope provenance.

The application wire exposes no worker target, file path, credential reference,
URL, callable, control signature or record-grant issuer. Python composition and
brokers remain trusted: arbitrary hostile same-process Python is not contained by
these checks. The separate kernel lab is the executable consumer-custody test.
Stop is synchronous between operations; urgent termination is external supervision,
not a claim of interruptible production revocation. No production transport,
metadata pagination protocol, protected audit service or live activation is added.

`tests/test_broker_metadata.py` exercises real broker/worker processes and local
adversarial cases. `tools/isolated_broker_probe.py` now requires brokered metadata
before record reasoning and rechecks both revocations after restart. Unconfined
negative controls cannot pass containment. Release statuses remain FAIL/BLOCKED;
the read-only report's metadata blocker now names missing **production** proof.
