# Offline customer login session boundary, issue #262

This draft adds a server-side OIDC authorization-code/PKCE flow state machine,
not a public login endpoint. The authorization URL contains one-use state,
nonce and S256 code challenge; the PKCE verifier is held only in a private
0600 registry. The backend binds a reviewed HTTPS token exchange function at
service construction; an individual callback cannot substitute its provider.
No user-provided token URL or connector credential is accepted.
The state is atomically consumed before exchange, so a failed callback must
start a new login. The existing pinned-key `OIDCVerifier` validates the ID
assertion's signature, issuer, audience, nonce and freshness.

The requested organization identifier is untrusted until the verified IdP
identity passes the tenant registry's membership check. The ID assertion's
tenant claim is ignored. Successful login creates a random, five-minute
opaque browser session; only its SHA-256 is persisted. The cookie policy is
`__Host-`, Secure, HttpOnly, SameSite=Lax and Path=/, with no Domain. A
separate CSRF token is required for mutating operations and logout; its hash
is stored server-side. Every session use rechecks tenant status, membership,
role, IdP-session revocation and expiry. Logout removes the browser session
and revokes its verified IdP session in the local registry.

All tests use synthetic identities and keys. The module does not select an
identity provider, register an OIDC client, implement an actual token exchange
or callback HTTP handler, set a browser cookie, enroll connector secrets,
expose a customer service, or authorize ERP access. Production qualification
still requires provider/client configuration, trusted key rotation, origin
and CSRF integration tests, rate limits at the HTTP edge, independent security
review and an operator-controlled credential vault.
