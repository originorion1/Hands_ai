# Customer identity boundary (draft, issue #243)

This offline module is a prerequisite for customer onboarding, not a public
login or enrollment service. It exposes no HTTP route and does not contain an
IdP client secret, connector credential, customer record or private signer.

The operator configures one exact HTTPS OIDC issuer and audience and supplies
its reviewed RSA public keys outside the repository. A login callback must
first establish a server-held nonce through an established OIDC authorization
code flow. `OIDCVerifier.verify` then validates the signed ID token, exact
issuer/audience, nonce, freshness and signature. It deliberately discards any
tenant claim in the token. Its ORION session identity expires within five
minutes and may be revoked in the private tenant registry. A future callback
and secure HttpOnly session cookie are required before browser login exists.

The first administrator is bound through a separate Ed25519-signed onboarding
approval, consumed once in an atomic SQLite transaction. The private approval
key is kept outside this service. The approval names the verified IdP subject,
tenant identifier, organization, country, timezone, sector, data residency,
retention duration and expiry. `TenantRegistry.authorize` checks current
membership, role, active tenant and session revocation for every call. A
user-supplied tenant identifier alone cannot grant access. The registry file
must reside in a private directory; it contains no connector secrets.

The connector catalog accepts only exact reviewed installed package metadata
and a separately reviewed SHA-256-bound field schema. It rejects duplicate
keys, unknown fields, credential values and secret fields that are not
write-only. Catalog registration does not import connector code or grant
execution authority.

The customer auth dependency set is in `requirements.lock`, installed with
`--no-index --require-hashes` from an operator-reviewed wheelhouse. The PyJWT
2.15.1 wheel hash was checked against the official PyPI release record; its
source is MIT licensed. The verifier uses RS256 exclusively and never follows
token-supplied key URLs. [PyJWT releases](https://github.com/jpadilla/pyjwt/releases),
[JWT best current practice](https://www.rfc-editor.org/rfc/rfc8725/), and
[OpenID Connect Core](https://www.openid.net/specs/openid-connect-core-1_0.html)
inform the validation boundary.

Open gates: OIDC authorization-code client and provider configuration, browser
session cookie and CSRF protection, MFA/enterprise SSO policy, admin/member
invitation proof, protected credential vault and enrollment endpoint, connector
destination review, authorization wizard, tenant lifecycle, retention and
backup, full stable-Linux regression, and independent security approval.
Nothing in this draft authorizes a customer ERP read or a public service.
