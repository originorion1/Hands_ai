# Proposed Terrace Beirut metadata-only pilot authorization

**Status: NO_GO.** This is an owner-review proposal, not an active grant or
permission to contact the ERP. No credentials, customer records or live TLS
observations were used to prepare it. The original owner-proposed identifier
is preserved separately from the canonical slug.

| Control | Proposed exact value | Approval or verification state |
| --- | --- | --- |
| Customer | Terrace Beirut | Proposed by owner; customer authorization pending |
| Canonical tenant slug | `terrace-beirut` | Format valid; provisioning and membership pending |
| Original external reference | `terrace-Beirut` | Owner-proposed identifier; preserve exactly |
| ERP company | **UNVERIFIED** | Obtain an exact company ID from a customer administrator's signed mapping, then bind it to the enrolled connection and grant; do not infer it from the hostname |
| Candidate ERP origin | `https://terrace-beirut.erpmax.me` | Owner-proposed; origin, DNS, TLS certificate chain/hostname and egress route review pending; no contact yet |
| Connector | `originorion-connector-erpnext-discovery==0.2.0rc4` | Exact wheel/sdist hash and installed entry point to be supplied by candidate artifact manifest |
| Customer runtime | `originorion-customer-runtime==0.1.0rc5` | Draft only; exact artifact and installed-runtime qualification pending |
| Read method | `GET /api/method/frappe.desk.form.load.getdoctype?doctype=<approved name>` | Metadata schema only; direct TLS, no proxy, no redirects; no unrestricted resource/record reads |
| Proposed DocTypes and fields | `Purchase Order`: `supplier`, `transaction_date`, `items`; `Purchase Order Item`: `item_code`, `qty`, `uom`, `rate` | **UNVERIFIED/UNAPPROVED** field existence and sensitivity; owner and customer must approve exact list before grant issuance |
| Historical period | None | This session requests schema metadata only, no transaction history |
| Exclusions | Payroll, personnel, contact details, credentials, attachments, comments, free text, financial account details, raw purchase records | Pending customer review; field allowlist is authoritative |
| Request budget | Maximum 25 attempted metadata GETs | Enforced before transport; request 26 denied; no retry grant |
| Duration | Maximum 30 minutes monotonic and wall-clock | Exact UTC start and expiry **UNSET** until approval; expiry must be no later than start + 30 minutes |
| Evidence volume | Maximum 64 KiB per response and 256 KiB projected total | Enforced; reject oversize response or projection |
| Retention | Proposed 1 day after grant expiry | **UNAPPROVED**; signed grant binds days; private state purge is available only after the deadline; operator/scheduler assignment pending |
| Credential reference | `vault:<owner-provided opaque reference>` | **UNSET**; value must be supplied and reviewed privately, never written into this packet or a browser response |
| Owner signing key | External protected owner-controlled Ed25519 key | **UNPROVISIONED/UNREVIEWED**; no production signing activation |
| IdP and session | Reviewed OIDC issuer, client, callback, keys and administrator membership | **UNPROVISIONED/UNQUALIFIED** for this customer |
| Revocation operator | **UNASSIGNED** | Name a human on call and verify ability to revoke signed-grant ledger and vault reference |
| Independent reviewer | **UNASSIGNED** | Security and outcome reviewer appointments pending |

## Exact company mapping procedure

1. The customer administrator supplies the exact ERP company identifier and
   external reference through an approved, authenticated channel; record the
   approver and immutable attestation digest outside public source control.
2. The owner reviews that mapping against the intended tenant slug and
   enrolled connector profile. The portal's browser tenant value is not
   authority; membership is checked against the verified IdP session.
3. If a live cross-check is required, include that operation in a **separate**
   explicit read-only grant. This metadata-only proposal cannot enumerate or
   read Company business records. Any mismatch aborts the session.

## Approval and launch sequence

1. Review destination ownership, DNS and TLS certificate hostname/chain via
   an owner-approved bounded verification operation. Configure direct egress
   policy for only the reviewed destination; prevent private-address or
   unreviewed redirect routing. This packet alone authorizes no network call.
2. Customer and owner approve the exact company, fields, start/expiry UTC,
   vault reference, evidence/retention limits, revocation operator and
   independent reviewer. Issue a signed `approval_packet` bound to the
   separately reviewed portal proposal. Customer consent alone does not issue
   a grant.
3. Verify the installed wheel/sdist SHA-256, connector entry point, private
   registry and approval-ledger permissions, OIDC/vault configuration and
   stable-Linux exact-head suite. Run the synthetic installed rehearsal and
   independent security review before live activation.
4. The operator starts `run_confined_metadata_session` once with the exact
   approved grant. The runtime consumes the grant before resolving the vault
   reference. It reserves each attempt before transport, checks revocation
   before and after reads, and persists only projected metadata under a
   private directory. It persists the structural study under the active grant
   lock before marking acquisition consumed. Bind the returned source with
   `EnrolledMetadataConnection.report_binding`; it uses the current UTC clock
   for each read. Report reads require the same
   enrolled connector, completed ledger status, current session membership and
   unexpired, unrevoked grant. No ERP writes or production execution are available.
5. Emergency stop: revoke the approval ledger, disable the vault reference,
   block destination egress, and stop the process. Retain partial state for
   custody review until its approved deletion deadline; a restart cannot reuse
   the consumed grant. Reissue requires a fresh review and signature.

**Current decisions needed:** customer company mapping and field approval,
fresh owner/customer authorization, IdP/vault provisioning, destination/TLS
and egress approval, UTC window, retention operator, independent security
review, and an authorized stable Ubuntu runner. Until every item is resolved,
`AUTHORIZATION_READY=BLOCKED` and `LIVE_ERP_METADATA_PILOT_READY=BLOCKED`.
