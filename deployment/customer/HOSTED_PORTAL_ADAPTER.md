# Hosted customer portal adapter

`create_hosted_customer_portal` composes the existing OIDC verifier, one-use
code/PKCE exchange, five-minute server session, tenant registry, report service,
connector enrollment and HTTPS WSGI portal. The returned callable is mounted by
a **separate stateful customer backend** at one reviewed HTTPS origin. It is not
mounted in the Vercel static Command Center. The existing `vercel.json` and
public staging routes remain unchanged.

## Host-owned configuration

At startup, the operator's private bootstrap supplies:

- Exact public HTTPS origin and callback URI on that origin; exact OIDC issuer,
  authorization and token endpoints, client ID, reviewed pinned RS256 public
  keys and a `vault:` client-secret reference. Key rotation needs a reviewed
  restart/reload process. The real provider must be independently qualified.
- `TenantRegistry` on durable private storage with its public onboarding key.
  The backend has no private onboarding or grant-signing key.
- Server-only vault callbacks for the OIDC client secret and one-time ERPNext
  connector enrollment. The vault must issue unique, revocable references;
  raw values are never returned to the browser or written to the registry.
- One reviewed connector distribution/schema digest and separately reviewed ERP
  origin for enrollment. Enrollment grants no metadata read.
- `report_lookup(tenant_id)` returning only a trusted `CustomerReportBinding`
  for that tenant. A binding must use a custody-verifying source and the
  completed signed-grant ledger's fresh revocation/expiry checks. Browser input
  cannot construct the binding.

The HTTPS reverse proxy must send the original exact `Host`, set the WSGI scheme
to `https` only for a connection it terminates, reject spoofed forwarding
headers from clients, impose request-size and rate limits, and route only
`/customer/*` to this application. The WSGI app itself denies other scheme or
host values, responds with no-store headers, and uses `__Host-` Secure,
HttpOnly, SameSite=Lax session cookies. A separate readable CSRF cookie is
required for mutations. No token, credential, or customer record belongs in
process arguments, source files, public assets, URLs, or access logs.

## Governed workflow

The browser can submit a review-only authorization proposal. A distinct owner
approval process retrieves the proposal with `CustomerPortalService` under an
administrator session, validates an exact `approval_packet`, and issues a
signed `ApprovedMetadataGrantLedger` outside this HTTP app. The trusted operator
may then invoke `run_confined_metadata_session` once with the approved
`EnrolledMetadataConnection`. This path enforces the request, duration, field,
evidence, retention, TLS and read-only bounds already owned by the metadata
runtime. The operator binds the persisted source through
`connection.report_binding(source)` in the host's tenant-indexed lookup.
Revoking the ledger or connector immediately denies subsequent report reads.
No browser route issues grants or starts discovery.

## Synthetic verification and open release gates

`tests/test_hosted_customer_portal_285.py` exercises the factory with a fake
OIDC token endpoint, private synthetic vault, two tenants, three users,
administrator/analyst separation, enrollment, proposal-only status, report
tenant binding, grant revocation and logout. Existing metadata tests cover
signed grant issuance, attempt 26, expiry, interruption and source-to-report
custody. The portal Node interaction test covers displayed-data clearing on
denial. These are offline synthetic tests; they do not qualify a live provider,
vault, HTTPS proxy, protected Preview or customer ERP access.

Before hosting for real, provision and independently review the IdP, vault,
durable state, egress and TLS route. Run actual browser acceptance, exact-head
stable-Linux full suite, package install tests and independent security review.
The Terrace Beirut packet remains NO_GO until its company/scope, credential
references, UTC window, retention, destination and fresh authorizations are
approved. Keep `execution_allowed=false` and ERP writes at zero.
