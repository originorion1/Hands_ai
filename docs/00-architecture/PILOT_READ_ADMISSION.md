# Pilot read admission boundary

Status: implemented on a feature branch; independent review pending.

## Existing paths and dependencies

Canonical at inspection: 40c02b3. PR #124 adds optional ReviewedReadWindow checks
to historical sampling; this branch includes its exact 119831d dependency.
PR #119 is a separate prospective ledger. PR #121 composes that ledger and events.
Neither prediction branch is required for admission, so neither is bundled here.

Existing governed study runners reauthorize some reads, but DiscoveryAdapter's
public discover contract and legacy adapters do not enforce one global gate.
This change creates a dedicated pilot boundary, not a retrofit or sandbox for
every older networking path. Legacy live-session launchers remain outside it.
Do not start a pilot through a legacy launcher assuming this policy applies.

## Application interface

The trusted control plane supplies exactly one PilotAuthorization for a stable
authorization_id. It contains source_id, ReviewedReadWindow (tenant, company,
resource, exact fields, source Date field, inclusive start/end, exclusive expiry),
identity_field, company_field, provenance_source, evidence_kind and max_records.
No field has a default authorization. A request separately carries those scope
coordinates and its record bound, but cannot supply a grant object to the launcher.

Call launch_pilot_read(request, authorization_id=reference, lookup=trusted_lookup,
adapter=configured_reader, clock=trusted_clock). The lookup returns one grant or
None. Missing, malformed or ambiguous results fail closed. Grant construction is
not authentication: loading actual grants, verifying issuer/owner authority and
maintaining the lookup are trusted application responsibilities. No test grant
or environment variable is an implicit production approval.

An exact scope match is required, except the requested record count can be lower.
Changing date/field/resource semantics needs an explicit grant, not inferred
permission. The grant lookup is reconsulted before transport and after response;
revocation or any grant change rejects admission. The default clock is UTC now.
Injected clocks are test/trusted-runtime dependencies, not caller request fields.

## Enforcement order

1. Validate the request, lookup and grant; apply ReviewedReadWindow checks.
2. Match configured adapter source to the authorized source.
3. Issue a short-lived non-serializable PilotReadPermit.
4. Adapter uses that permit; the ERP bridge independently verifies the exact
   encoded HTTP method, origin, resource path, field/filter query and limits.
5. Claim the permit's single transport allowance immediately before I/O.
6. Existing historical adapter validates redirects, response size, company,
   submitted status, requested fields and source dates.
7. Reauthorize; validate all evidence and rows; freeze copied payloads; reauthorize
   once more before returning the whole batch. Close permit on success/failure.

The ERP bridge supplies no write method. Its raw historical adapter is created
inside read(permit), using the bound request and mandatory read window. Direct
read(request) or forged ordinary permits are rejected. Other trusted adapters can
implement PilotReader without ERP dependencies in the authorization module, but
must enforce their own wire encoding against the issued scope. The provided ERP
bridge is the only transport implementation verified here.

Python private members and permits are engineering guards, not cryptographic
capabilities against hostile code running in the same interpreter. Untrusted
plugins/models must not receive transport credentials, a mutable grant registry,
or arbitrary Python execution. Egress/process containment and removal of legacy
entry points from a deployment are prerequisites if that threat model is needed.

## Admission and provenance

Accept only bounded tuples of canonical read-only observations. Check evidence
kind/source/tenant, UUID identities, aware nonfuture source observation time,
exact resource and field membership, company, identity and historical date.
Only flat JSON scalar record values are supported; reject nested/extra fields,
non-finite numbers, invalid confidence and duplicate identities within a batch.

The canonical Evidence and Observation types are preserved. Original evidence ID,
observation ID, source label and observation timestamp survive. Copied record
payloads and added provenance are immutable MappingProxyType values. Provenance
adds authorization_id, complete grant SHA-256, bound source_id and source record
identity. The digest records a scope, not a signature or proof of issuer authority.
No sink is called before complete batch validation; this API does not persist data.

Re-admitting the same source observations with the same grant preserves identities
and produces equal evidence. Separate live acquisitions may have different source
IDs/timestamps. There is no durable request cache, cross-run deduplication or
exactly-once acquisition claim. Downstream stores must preserve the mapping-based
payload and the source IDs; serializers that blindly deepcopy mapping proxies
need an explicit mapping encoder. Persistence is deliberately not added here.

## Supported scope and remaining release gates

The initial ERP bridge is limited to the existing bounded submitted-record reader:
source Date fields, required audit fields, at most 25 records, one GET and flat
records. It rejects unsupported configurations before I/O. Undated masters,
source timestamps, other vendors and arbitrary full-dataset ingestion are not
silently mapped onto this contract. Source and field semantics must be reviewed
when the operator supplies an actual grant and adapter mapping.

Expiry equality is denied. A response completing after expiry is rejected, but
an already-issued request cannot be recalled; bytes may have entered transient
memory. This boundary returns no rejected batch and persists nothing.

Before an actual pilot: independent exact-commit review; governed merge or approved
release; trusted operator grant lookup and source mapping; private credentials;
route that deployment only through launch_pilot_read; verify downstream storage
encoding privately. No actual customer information, grant, endpoint or credential
is supplied by this change. No live connection or write operation was performed.
