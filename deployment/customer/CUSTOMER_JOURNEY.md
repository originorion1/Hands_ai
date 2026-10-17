# Offline synthetic customer journey, issue #243

This branch composes the existing customer identity, tenant lifecycle and
continuous lineage drafts on canonical. It does not expose a public login or
enrollment route. The issue #243 contract keeps customer access closed until
independent review.

## Identity and role boundary

The operator pins the OIDC issuer, audience and RSA verification keys. The
backend starts a one-use authorization-code/PKCE attempt, consumes the state,
verifies the ID token and binds a short-lived HttpOnly, Secure, SameSite=Lax
session to a currently active tenant membership. The ID token's tenant claim
is ignored. Every use rechecks membership, tenant status, revocation and
expiry. Mutations and tenant switching require CSRF; switching rechecks
membership for the target tenant. The IdP owns SSO and MFA policy. Production
use needs reviewed IdP configuration, callback routing and key rotation.

## Connector enrollment boundary

`enroll_erpnext` is a backend-only function. It checks the administrator
session and CSRF before handling secrets, then checks installed connector
metadata against an operator-reviewed registration and digest-bound schema.
The schema must specify endpoint, API key, API secret and company. The endpoint
must be an HTTPS origin equal to the operator's separately reviewed origin;
loopback, private and link-local IPs, credentials in URLs, paths and non-443
ports are rejected. No network request occurs in enrollment. The backend vault
receives the API key and secret; the application registry stores only its
opaque reference. A registry failure invokes vault revocation. Registration
and enrollment grant no execution or ERP read authority. The existing
governed read permit remains mandatory before acquisition.

The protected backend interface and vault implementation must be independently
reviewed before a public endpoint is added. A later connector call must pin
DNS resolution and TLS identity to the reviewed destination; this offline
enrollment cannot by itself make outbound traffic safe. No real ERP endpoint
or credential is used by the synthetic tests.

## Evidence and lifecycle

The lineage drill first completes a one-use synthetic OIDC code flow and
verifies the signed tenant bootstrap and active browser session. The
`run_customer_synthetic_lineage` boundary rechecks that session and rejects a
tenant mismatch before invoking the one-use governed read authorization. It
then uses one unique synthetic source and run, a reviewed synthetic connector,
Kernel evidence custody,
investigation over the acquired observations, a persisted digest-bound report,
an authenticated HTTPS report host, the Vercel API route modules and the
Command Center browser UI. The private source, evidence, witness and report
files and the revoked synthetic session registry remain in owner-only custody
after the drill. The browser must remove
report data after an invalid token. Tenant lifecycle tests cover bootstrap,
roles, suspension/reactivation, rotation/revocation of references, retention,
backup/restore, offboarding and cross-tenant denial.

## Qualification boundary

The offline tests prove the modules' contracts and the synthetic lineage
path. They do not prove a deployed customer login, actual IdP MFA policy,
external vault custody, live connector capability test, protected public
Preview of this branch or production release. Independent security review and
signed release approval remain separate gates.
