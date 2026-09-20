# Governed production read-only discovery

## Scope and verdict

Issue #193 adds one installed launcher for a bounded ERPNext discovery milestone.
It does not declare the full product live-ready, authorize a customer connection,
or permit customer-system writes. The unchanged full-product report from
`orion.pilot.readiness.release_report()` remains `live_ready=false` and
`execution_allowed=false`.

The new manifest/profile v6 mode is
`governed_erpnext_discovery_read_only`. It starts with one metadata grant and no
record owner. A record owner can exist only after the separately witnessed v2
transition and a later explicit arm. Neither discovery nor startup creates record
authority. No recommendation, prediction, semantic promotion, or write operation
is part of this milestone.

## Authoritative owners

| Invariant | Owner |
| --- | --- |
| Exact HTTPS origin, DNS address set, TLS identity and lifetime | `orion.pilot.production.destination_from` |
| Customer/session scope, exclusions, prior consumed sessions and limits | `orion.pilot.production.access_ledger_from` |
| Artifact, profile, service and reviewed network namespace | `orion.pilot.production.host_attestation_from` |
| Independent approval of the exact ledger and host record | `orion.pilot.production.approval_from` |
| Exact ERPNext GET encoding | `orion.pilot.gateway.erpnext_source_request` |
| Credential use, hostname TLS and reviewed `--resolve` tuples | `orion.pilot.gateway.CredentialGateway` |
| Kernel egress allowlist and gateway-only networking | `orion.pilot.isolation` and the reviewed host namespace |
| Grant, attempt budget, audit, admission, evidence and witness lineage | Existing pilot authorization/custody owners |
| Fixed installed launch and scope report | `orion.pilot.deployment` and `orion.pilot.readiness` |

These owners are composed, not copied. Package data, ERP responses, requests and
environment variables cannot select an origin, address, port, proxy, redirect,
header, module, callback or launcher.

## Version and compatibility boundary

Versions 1–4 and their `.test` source guard are unchanged. Manifest/profile v5
keeps `erpnext-record-grant-transition-v1`, including its synthetic `.test`
restriction. Version 6 alone requires
`erpnext-record-grant-transition-v2`; its exact source must equal the reviewed v6
destination origin. There is no silent migration.

The v6 destination has one canonical HTTPS origin with an explicit port, one to
eight sorted numeric addresses, the same DNS hostname as its TLS server name, a
pinned trust-file digest, and bounded resolution timestamps. Local qualification
accepts only `.test`, port 44443 and reserved documentation addresses. Reviewed
external mode rejects qualification names and addresses.

At an external start or restart, DNS resolution must equal the signed address set.
The gateway then uses the hostname URL for TLS verification and fixed `--resolve`
entries for those addresses. It does not perform destination selection from a
request. Default-deny host policy permits only the signed addresses and port;
redirects, proxies, UDP, other ports and other addresses remain denied. A changed
resolution, expired contract, changed certificate file, or TLS hostname mismatch
requires a new reviewed destination and new bound private evidence.

## Private evidence and startup order

All paths below are owner-only files or directories and are never checked into the
repository. The operator provisions:

1. The approved wheel in a dedicated installed environment and its hashed
   `RECORD` identity.
2. Private state, witness and key directories, mode 0700, preserving all prior
   custody and consumed-session records.
3. The credential and canonical issuer references already named by the grants.
4. A separate `deployment-approval` key controlled independently of the package
   and ERP data.
5. The v6 destination, metadata-only configuration, v2 transition envelope and
   exact deployment profile.
6. An access ledger naming the deployment/artifact/destination/config/envelope,
   tenant/company/site schema, credential references, exclusions, prior consumed
   sessions, expiry and request/byte/time ceilings.
7. An issuer-authenticated approval over the exact ledger digest.
8. A host attestation naming the exact artifact/profile/destination/ledger, the
   external exact-commit installed-qualification receipt digest, reviewed service
   entrypoint, distinct network namespaces and all required host controls.
9. A separate deployment-controller approval over the exact host attestation.

The runtime authenticates all of that before enrollment/state creation. External
startup additionally validates current DNS and the reviewed namespace before
constructing services. Missing, malformed, stale, broadened or mismatched evidence
returns a non-secret denial. There is no permissive default and no evidence value
can supply its own approval key.

The reviewed external service must already be placed in its dedicated network
namespace. The launcher does not create a synthetic source or attach a production
destination to the local laboratory fabric. Custody, authorization, acquisition
and reasoning processes still receive no network. Only the credential gateway
inherits the reviewed egress namespace.

## Inspection, enrollment and launch

The non-connecting inspection command is:

```sh
cd /
env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  /operator/runtime/bin/python -I -m orion.pilot.readiness \
  --discovery-manifest /operator/private/runtime-manifest.json
```

With no manifest it reports the five missing prerequisite groups. Invalid private
evidence reports `bound_private_deployment_evidence_invalid` without printing a
path, identifier, credential or customer value. A local-qualification manifest
reports `qualification_ready` but `ready_for_unarmed_startup=false`; it cannot use
the reviewed launcher.

After exact-commit review, integration, private provisioning and host checks, the
one-time witness enrollment uses the fixed command in
`WHEEL_ONLY_CANDIDATE_LAUNCH.md`. Only a reviewed-external report may be started:

```sh
cd /
env -i PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
  /operator/runtime/bin/python -I -m orion.pilot.readiness \
  --discovery-manifest /operator/private/runtime-manifest.json --start
```

That command replaces itself with the profile-bound installed
`python -I -m orion.pilot.deployment --serve ...` entrypoint. It starts unarmed.
A separately authenticated metadata arm is still required before the first
bounded source call. Record provisioning and record arm remain distinct later
actions. `execution_allowed=false` continues to mean no customer-system writes.

Stop durably stops enrolled operations, terminates acquisition and the only
credential-bearing egress process, and preserves consumed attempts, audit,
evidence and witness state. Restart revalidates the evidence and starts unarmed;
it does not refund budgets or restore authority.

## Scope-specific release mapping

The v1 discovery report maps every full release category. `NOT_APPLICABLE` means
the milestone has no corresponding behavior; it does not turn a full-product
failure into a pass.

| Category | Discovery obligation/evidence | Scope status after exact valid private evidence |
| --- | --- | --- |
| SECURITY | Exact artifact/profile, closed launcher, process separation and reviewed default-deny egress | PASS |
| EPISTEMIC_SAFETY | No recommendation, prediction or promotion exists | NOT_APPLICABLE |
| AUTHORIZATION | Authenticated ledger plus distinct metadata and record grants | PASS |
| DISCOVERY | One bounded metadata-only ERPNext scope | PASS |
| SEMANTIC_UNDERSTANDING | No business-semantic claim exists | NOT_APPLICABLE |
| WORLD_MODEL | No world-model publication or mutation exists | NOT_APPLICABLE |
| PROVENANCE | Canonical API admission and evidence lineage | PASS |
| RESTART | Retained audit/evidence/witness, unarmed restart, no refund | PASS |
| TRANSPORT | Exact hostname/address/port/TLS, no redirect/proxy/alternate route | PASS |
| SECRETS | Reference-only private keys and gateway-only credential mount | PASS |
| TENANT_ISOLATION | Ledger and grants bind exact tenant/company/source | PASS |
| AUDIT | Durable request attempts, transition records and witness progress | PASS |
| OBSERVABILITY | Secret-free local status, counters and audit output | PASS |
| FAILURE_SAFETY | Fail-closed validation, durable stop and gateway cutoff | PASS |
| DATA_MINIMIZATION | Metadata-first scope, exclusions, byte/request/time limits | PASS |
| COST_CONTROL | Ledger and journal ceilings; failures remain consumed | PASS |
| ERP_GATEWAY | Existing fixed read-only ERPNext encoder and adapter | PASS |
| TEST_COVERAGE | Exact installed v6 local qualification plus boundary regression suite | PASS |
| DEPLOYMENT_CONFIGURATION | Approved wheel/profile/service/namespace attestation | PASS |

The report retains `allow_live_customer_access=false` because it is an inspection
result, not a connection receipt. A later real bounded run may report attempted
reads and admitted persisted evidence; readiness alone cannot claim either.

## Qualification and remaining obligations

Local qualification builds and installs a clean wheel, verifies `RECORD`, uses a
hostname-valid disposable certificate and ordinary Frappe-format HTTPS server,
and exercises v6 with reserved addresses. It proves exact GETs, kernel address and
port denial, metadata-first authority, missing/mismatched transition approval,
separate provision/arm, admitted persistence, restart and durable stop. It never
uses customer credentials, identifiers, hosts or networks and cannot authenticate
a real operator or reviewer.

Before an actual connection, maintainers still must integrate the predecessor
stack in canonical order, obtain independent exact-commit code/security review,
approve the exact wheel and private host configuration, provision a fresh
non-consumed access ledger and grants, confirm expiry/revocation and emergency
ownership, and explicitly authorize that one bounded launch. A real attempted
read, admitted evidence and verified persistence remain future operational facts.
No test, draft PR or readiness report supplies them.
