# Customer portal review boundary

This draft composes the existing offline OIDC, tenant registry and ERPNext
enrollment with a same-origin HTTPS WSGI adapter. It does not start a listener,
choose an identity provider, supply a vault, create a Kernel grant or contact an
ERP. The existing synthetic staging route is unchanged.

## Implemented offline behavior

- An OIDC code-flow callback creates a short-lived, revocable, tenant-bound
  session. A pending invitation can be consumed only by the exact verified IdP
  issuer and subject. Every tenant switch and mutation checks the current
  session and CSRF token. The browser clears scoped data on denial and service
  interruption.
- Administrator invitations, removals and last-administrator protection are
  stored in the private tenant registry. Offboarding clears invitations,
  connection profiles, external company references and pending scope proposals.
- The signed organization bootstrap can store an original ERP company reference
  separately from the canonical tenant slug. `OrganizationProposal` is a
  validation and review form; it does not provision a tenant.
- The ERPNext endpoint accepts a credential submission only with a reviewed
  connector schema, operator-approved HTTPS origin, administrator session,
  CSRF token and server-side vault callbacks. Browser responses omit both raw
  credentials and vault references. Enrollment does not authorize a read.
- The read-scope wizard records a digest-bound proposal with explicit companies,
  DocTypes, fields, exclusions, history, request/time/evidence ceilings,
  retention and revocation operator. Its status is always
  `pending_governed_validation`; no execution grant is issued.
- The portal overview reports only the authenticated tenant's organization,
  role, connection status and current absence of a bound grant. It does not
  fabricate studies, evidence coverage or findings.

## Independent release gates

This code has no reviewed live IdP configuration, public TLS route, protected
vault service, invitation delivery channel, approved external company mapping,
Kernel grant binding, report/findings binding or independent security decision.
The generic WSGI adapter must not be mounted publicly until its trusted TLS
termination, host/origin validation, request logging and error handling are
reviewed. A synthetic provider proves the code flow contract only; it is not a
real customer login acceptance test. Customer ERP access, production signing,
package publication and promotion remain prohibited.
