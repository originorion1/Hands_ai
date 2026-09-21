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

## Post-discovery provisioning contract

Issue #191 adds manifest/profile v5 for metadata-only enrollment and one
operator-authenticated, generation-zero-to-one record-grant transition. The
enrolled envelope and transition witness stream bind the immutable deployment,
artifact, tenant, source and budget ceilings without naming or hashing the future
resource, fields or grant. The exact grant is accepted only after current-start
metadata is admitted and its retained evidence references and current witness
snapshot are bound into the issuer-authenticated request.

The existing evidence, audit, authorization, gateway and acquisition owners commit
the transition in that order. The record owner is created with its transition
reference in the existing `AttemptJournal`, the existing witness is advanced, and
the effective grant remains unarmed. There is no manifest rewrite, replacement
witness, second store, budget reset, refund or authority restoration. Restart
validates the same lineage and reconstructs the owner unarmed. See
`POST_DISCOVERY_GRANT_TRANSITION.md` for the commit and failure contract.

Manifest/profile v4 remains the preloaded candidate contract. It must not be
reported as post-discovery provisioning and is not silently migrated to v5.

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

Post-discovery provisioning is the separate manifest/profile v5 pair with mode
`candidate_erpnext_post_discovery_read_only`. Its initial config list contains
metadata only and its closed `erpnext-record-grant-transition-v1` envelope carries
no placeholder read authority. Versions 1–4 reject the transition field; v5
rejects preloaded record configs and mixed versions.

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

The v5 scenario additionally proves that opaque record resource/field names and
the future grant digest are absent from the initial manifest, missing or invalid
controller authentication leaves transition state and record I/O unchanged,
provisioning remains unarmed at metadata 2 / records 0, and restart preserves the
consumed record budget and audit prefix while restoring no active authority.

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
  -k 'erpnext_candidate or erpnext_grant_transition' -p no:cacheprovider
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

Issue #193 adds a separate manifest/profile v6 production-capable composition;
it does not widen v4/v5. Its destination, private approvals, scope-specific gate
mapping and installed qualification are specified in
`GOVERNED_PRODUCTION_DISCOVERY.md`.
