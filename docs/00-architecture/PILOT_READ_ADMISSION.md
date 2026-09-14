# Pilot read admission boundary

Status: implemented on a feature branch; independent review pending.

## Existing paths and dependencies

Canonical at inspection: 40c02b3. PR #124 adds optional ReviewedReadWindow checks
to historical sampling; this branch includes its exact 119831d dependency.
PR #119 is a separate prospective ledger. PR #121 composes that ledger and events.
Neither prediction branch is required for admission, so neither is bundled here.

Existing governed study runners reauthorize some reads, but DiscoveryAdapter's
public discover contract and legacy adapters do not enforce one global gate.
Legacy ERP default openers and the generic HTTP default fetcher now reject
all calls locally. This intentionally disables their implicit live networking,
including metadata/preflight/refresh and old session/trial runners. They are not
silently granted the pilot's scope. Explicit injected transports remain available
for offline testing and trusted composition, not as an authorization mechanism.
There is no built-in raw network opener. pilot_transport.open_pilot_read dispatches
only to an explicitly supplied trusted callable after permit/wire validation.
The existing source/capability policy forbids raw networking imports and is unchanged.

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
5. Bind the exact validated URL/method/body/headers to the permit once. The
   neutral transport checks that binding, HTTPS/GET/no body and bounded timeout,
   consumes one allowance, then rechecks expiry and revocation immediately before
   dispatch to the supplied transport. Mutated/reused/unbound requests fail.
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
or arbitrary Python execution. Explicit injected callables can execute arbitrary
Python, including network calls; they are trusted dependencies, not sandboxed.
Hostile plugins require OS/process egress containment. The source-inventory test
checks the absence of built-in networking imports; it is a regression gate, not a security proof
against obfuscated imports or deliberate monkeypatching.

## Admission and provenance

Accept only bounded tuples of canonical read-only observations. Check evidence
kind/source/tenant, UUID identities, aware nonfuture source observation time,
exact resource and field membership, company, identity and historical date.
Only flat JSON scalar record values are supported; reject nested/extra fields,
non-finite numbers, invalid confidence and duplicate identities within a batch.

The canonical Evidence and Observation types are preserved. Admission derives
versioned UUID5 identities from the complete grant digest, tenant, resource,
canonical row, source/kind, observation timestamp, confidence and mode. Random
upstream UUIDs do not affect admitted identity; they remain attached as
upstream_evidence_id and upstream_observation_id in immutable provenance.
Copied record payloads and provenance use MappingProxyType. Source timestamps
and labels survive unchanged. The grant digest is not proof of issuer authority.

Freshly constructed observations with identical acquisition content have identical
admitted IDs. Different observation timestamps represent different acquisitions.
Upstream provenance IDs may differ between fresh constructions, so complete object
equality is not promised. No durable cache or exactly-once acquisition is provided.
Downstream persistence needs a mapping-aware serializer. No sink is called here.

The ERP bridge rejects unsupported provenance source and evidence kind before
constructing its historical adapter or contacting transport. Grant lookup is
reconsulted before transport and after response; a changed grant is rejected.

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

## Network inventory and migration

- ERP discovery, historical, metadata, company and identity readers share the
  now-denying legacy opener.
- Preflight/retry, metadata refresh, live session, bounded trial, six-hour
  continuation and learning comparison default to that same denying opener.
- Generic HTTP discovery also denies default network calls.
- ERPNextPilotReader validates its wire encoding and delegates to the neutral
  permit-gated transport. No built-in production opener is provided.

Existing CLI scripts using legacy defaults now fail closed even if credentials
and their older ledgers are present. Do not restore the old opener or inject a
raw live opener to work around this. A separate metadata authorization contract
would be needed to reactivate metadata discovery; a record grant is not one.
Tests exercise all legacy opener aliases, direct adapters, permit/wire tampering,
expiry before transport, and the absence of built-in raw network imports.

A deployment must supply a reviewed HTTPS transport with redirects disabled,
bounded timeouts and response handling through the existing historical reader.
The callable is a trusted runtime dependency, not a request/configuration value.
No production transport was fabricated or activated in this offline change.

Adapter failures expose only one of three stable categories:
scope_or_response_invalid, upstream_read_failed, unexpected_internal_failure.
Raw exceptions/row contents are not logged or returned. These categories aid
triage but do not replace an independent audit or claim detailed root causes.

Historical sample evidence timestamps use the same trusted injected clock as
window enforcement, captured once per returned batch after the response check.
This is acquisition time, not a business date or a source-system event timestamp.
Full-reader tests with a frozen clock verify deterministic admitted identities;
advancing that clock produces a distinct acquisition. Header-copy tests cover
mixed-case request headers without weakening exact wire binding.
