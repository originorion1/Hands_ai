# Native ERPNext installed candidate qualification

Issue #186 adds one offline-qualified ERPNext read protocol to the existing
installed supervised runtime. It does not authorize customer access, production
credentials, activation, merge, or release-gate promotion. Every exercised source
uses a local HTTPS fixture on reserved documentation addresses with a `.test`
source identity. `LIVE_PILOT_READY=false`, `execution_allowed=false`, and
`allow_live_customer_access=false` remain mandatory results.

## Contract ownership

| Requirement | Authoritative owner | Candidate composition |
| --- | --- | --- |
| Metadata and record authority | `MetadataAuthorization`, `PilotAuthorization`, canonical launchers | Separate externally supplied grants; metadata admission never creates record authority |
| Request scope and native path | `pilot.gateway.erpnext_source_request` | Exact catalog, permitted schema, and record GETs derived only from canonical grants and exact requests |
| Credential use and HTTPS | `CredentialGateway` | Gateway-only ERPNext token credential, pinned TLS, one reserved address/port, no redirects/proxies/caller headers |
| Durable attempt state | `AttemptJournal` through audit custody | Reservation precedes receipt redemption and source I/O; failures consume budget |
| One-use source permission | `AuthorizationCustody` and `RuntimeCustody` | A validated descriptor is bound to the offer; the gateway revalidates it before receipt redemption |
| Response translation | Existing `ERPNextPilotMetadataReader` and `ERPNextPilotReader` | Already received bounded bytes are normalized; the normalizer cannot select a destination |
| Evidence and provenance | Canonical admission plus `EvidenceCustody` | API evidence retains exact tenant/company/source/resource/window/authorization lineage before publication |
| Kernel and process isolation | Existing deployment/fabric/profile owners | Wheel-only isolated launch, gateway-only network, zero-network reasoning/custody roles |
| Recovery and cutoff | Existing runtime, audit, evidence, witness, stop/revoke owners | Restart is unarmed; budgets/witness progress persist; no pending-attempt repair or refund |

ERPNext mechanics remain in the adapter/gateway edge. Core evidence,
authorization, admission, persistence, and semantic contracts are unchanged.
Metadata produces structural candidates and explicit uncertainty; it supplies no
business interpretation or execution permission.

Structural-only metadata is a valid result: a non-submittable or otherwise
unsupported resource can retain declarations and UNKNOWNs while both executable
field lists remain empty. The installed broker and evidence-custody reconstruction
must preserve that result, rather than rejecting discovery or inventing a record
scope. The native normalization regression covers empty schemas and opaque typed
fields in non-submittable schemas. Dates without executable fields still reject.

## Outstanding post-discovery provisioning contract

The current installed acceptance preloads both metadata and record grants. Its
later record `arm` operation does not provision a newly discovered scope. Thus
issue #186's post-discovery operator-provisioning requirement remains incomplete.

This cannot be resolved by editing the manifest and restarting: `witness_streams`
hashes the complete configurations, `deployment_identity_for_manifest` binds those
streams, and the immutable witness enrollment receipt also binds the profile.
Changing the record grant changes the enrolled identity. Re-enrolling, replacing
the witness, creating another evidence store, or resetting attempt budgets would
break the existing recovery and rollback contract.

The required follow-up is a versioned, operator-authenticated grant-transition
contract across the existing deployment, authorization, audit, evidence and
witness owners. It must start with metadata authority only; bind one later exact
record grant to retained admitted metadata and the existing deployment; preserve
all earlier attempt/evidence chains and witness progress; keep record authority
unarmed until separately armed; and reject unauthorized, stale, replayed,
cross-scope, stopped or pending-acquisition transitions. Crash boundaries between
the owners must fail closed without budget refund or automatic repair. Installed
WSL acceptance must discover opaque resources/fields before supplying the record
grant and prove those properties across restart. The current v4 contract does
not implement that transition and must not be reported as satisfying it.

## Explicit version compatibility

Legacy manifest versions 1–3 retain `synthetic_read_only`, deployment profile v3,
Bearer credentials, and the fixed `/metadata`, `/records`, and `/instrument/N`
fixture routes. They are not silently migrated.

The candidate is an explicit manifest v4 / profile v4 pair with mode
`candidate_erpnext_read_only`, broker version `erpnext-candidate-broker-v1`, and
protocols `erpnext_metadata_v1` plus `erpnext_records_v1`. Mixed legacy/candidate
configs reject. Profile v4 adds only the fixed `erpnext_read_only_v1` network
protocol marker; it does not widen the existing reserved-address allowlist or add
a production destination selector.

## Installed acceptance scenario

`tests/test_packaged_runtime.py` builds a clean wheel offline, installs it into a
clean environment, verifies installed `RECORD` integrity, enrolls the retained
witness, and launches the isolated runtime in WSL. Its ordinary local HTTPS source
speaks unmodified Frappe response formats and normal `Authorization: token ...`;
it imports no ORION code and implements no ORION receipt or grant endpoint.

The candidate scenario proves these source-call boundaries:

- startup and record-before-metadata: metadata 0, records 0;
- wrong metadata scope: metadata 0, records 0;
- admitted bounded catalog plus one schema: metadata 2, records 0;
- record request while unarmed: unchanged at metadata 2, records 0;
- separately armed exact record request: metadata 2, records 1.

The final path list must be exactly one bounded DocType catalog GET, one permitted
`getdoctype` GET, and one bounded `Sales Invoice` record GET derived from the
grants. The admitted observation must be canonical `api` evidence from
`erpnext-historical-sample-read-only`, and semantic interpretation remains
`UNKNOWN`.

Focused contract tests additionally deny wrong tenant/source/resource/budget
scope before receipt redemption or source I/O, package-selected transport fields,
mixed protocol versions, invalid ERPNext token forms, malformed responses, changed
received bytes, and profile drift. Existing unchanged gateway/runtime tests remain
the authority for strict framing, redirect/proxy denial, expiry, revocation,
budget exhaustion, restart, witness progress, emergency cutoff, and legacy
compatibility.

Qualification command (non-connecting; WSL namespace capability required):

```sh
ORION_BUILD_PYTHON=/tmp/orion-pr175-builder/bin/python3 \
  .venv/bin/python -m pytest -q tests/test_packaged_runtime.py \
  -k erpnext_candidate -p no:cacheprovider
```

Exact final wheel, installed `RECORD`, deployment-profile, source-tree, and commit
identities belong in the immutable external qualification receipt generated from
the final tree. They must not be copied from an earlier run or embedded here before
the final tree is frozen.

## Clause-level release reconciliation

| Obligation | State after implementation | Remaining requirement |
| --- | --- | --- |
| Existing installed broker/gateway/admission path handles native ERPNext reads | Implemented and locally exercised | Independent exact-commit review |
| Metadata-first and separate record authorization | Implemented and locally exercised | Real grants must come from an independently authenticated control plane |
| Exact GET derivation and post-response provenance | Implemented and locally exercised | Production source lifecycle/DNS/address policy is not tested |
| Candidate-host configuration | Not supplied by this issue | Reviewed unprivileged service, immutable wheel transfer, private keys/state/witness paths, kernel tools and resource policy |
| Production destination/profile | Deliberately absent | A separately versioned, reviewed contract change; no allowlist relaxation |
| Customer authorization and credentials | Deliberately absent | Separate access ledger and privately provisioned credentials |
| Independent security/artifact/host evidence | Missing | Human-controlled external qualification and review |
| Release adjudication | Not performed | Maintainer decision after all critical gates; tests cannot promote them |
| Predictive or economic value | Not evaluated | Separate experimental protocol and independent outcomes |

## Operator boundary

The only permitted action for this issue is the non-connecting local qualification.
There is no customer activation command or enable-live shortcut. A future read-only
pilot requires all of the following, none of which this issue supplies: a separate
customer-access ledger, a reviewed production destination/profile contract, exact
artifact and host attestation, privately provisioned credentials, independently
issued metadata and record grants, satisfied critical release gates, and explicit
human maintainer authorization. Consumed sessions and private credentials must be
retained in their existing private custody; they are never reset, printed, copied
into evidence, or inferred from this qualification.
