# Witnessed post-discovery record-grant transition

Issue #191 adds one separately versioned transition to the installed ERPNext
candidate. It does not add customer access, a production destination, execution
authority, another grant registry, or another evidence/audit store.

## Stable enrollment contract

Manifest and deployment-profile version 5 start with exactly one canonical
metadata configuration. Enrollment also binds an
`erpnext-record-grant-transition-v1` envelope containing the immutable
deployment/artifact identity, tenant, company, source, caller, credential
references, exact transport limits, and bounded field/record/window ceilings.
The envelope contains no record resource, field name, authorization identifier,
or future grant digest.

The existing progress witness receives one transition audit stream derived from
the envelope. Its initial deterministic state means `unprovisioned` and carries
no `PilotAuthorization`, record binding, source receipt, or read authority. The
same enrollment receipt and witness database are retained for the deployment's
entire lifetime.

Legacy manifests/profiles 1–4 keep their exact configuration and witness-stream
semantics. They cannot use the transition protocol and are not silently migrated.

## Single ownership and transition ordering

The authenticated operator obtains a challenge only after current-start metadata
admission. The challenge binds generation zero, the exact deployment and envelope,
the retained metadata checkpoint and observation/evidence references, and current
audit, evidence, and witness heads. The independently issued exact record config
and challenge are MACed by the existing issuer boundary.

Under the deployment transition lock, owners commit in this order:

1. Authorization custody validates the caller MAC, exact canonical record config,
   admitted metadata proposal, expiry, envelope, predecessor generation, and that
   no acquisition or pending reservation exists. It does not mutate authority.
2. Evidence custody appends the one immutable transition record to its existing
   chain, pins the accepted tip, and conditionally advances the existing evidence
   witness stream.
3. Audit custody creates the record `AttemptJournal` with its configure event and
   immutable transition reference, pins its accepted tip, and conditionally
   advances the pre-enrolled transition audit stream.
4. Authorization custody installs the exact owner unarmed. The existing gateway
   and acquisition owners receive only the same exact binding. No source I/O
   occurs.
5. The operator receives an acknowledgement only after every owner reports the
   same generation/config/transition digest. A separate authenticated `arm`
   command remains mandatory before a record attempt.

There is no compensating delete, reset, budget refund, re-enrollment, or automatic
repair. A failure after any durable boundary can leave a deliberately uncertain
state. Restart validates the evidence transition, audit journal, protected tips,
and both witness streams as one lineage. Missing, reordered, rolled-back, or
partially acknowledged state blocks acquisition and publication. A fully
committed transition reconstructs the same exact owner but starts unarmed.

## Metadata and authority boundary

Evidence custody proves only that the requested resource and every executable
field were present in one retained admitted metadata proposal. The grant must
explicitly name technical identity, company, and date fields and keep them inside
that proposal. Declarations and structural UNKNOWNs do not infer business meaning
and cannot issue authority. Empty executable proposals remain valid metadata but
cannot support a record grant.

The native gateway continues to derive the exact GET solely from the canonical
grant and request. Credentials, TLS trust, destination confinement, one-use
receipt redemption, post-response admission, and evidence lineage remain owned by
their existing components.

## Release boundary

Installed qualification uses only an ordinary local HTTPS Frappe-format fixture
on reserved documentation addresses. `LIVE_PILOT_READY=false`,
`execution_allowed=false`, and `allow_live_customer_access=false` remain fixed.
No live launch is permitted without the separately required customer ledger,
production destination/profile, private credentials, host/artifact attestation,
independent review, satisfied release gates, and human maintainer authorization.

Manifest/profile v6 reuses this transition owner with the explicitly versioned
`erpnext-record-grant-transition-v2` envelope so its source can equal one reviewed
production destination. The v1 `.test` guard remains unchanged. See
`GOVERNED_PRODUCTION_DISCOVERY.md`; v6 still provisions unarmed and requires a
separate record arm.
