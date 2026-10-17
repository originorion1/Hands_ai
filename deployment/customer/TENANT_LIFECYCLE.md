# Synthetic tenant lifecycle (issue #244)

This offline control-plane slice exercises two synthetic tenants. It exposes no
HTTP routes and does not access customer systems. The private SQLite registry
is created with restricted file permissions in an owner-controlled directory.
The registry persists its last observed wall-clock second and denies any
later connection if the host clock moves backward. This protects local
session, approval and retention checks during rollback; an independently
trusted clock source is still required for production qualification.

- Initial administrator membership comes from a verified IdP identity and a
  purpose-bound, one-use signed organization approval. Logged-out IdP sessions
  cannot consume approvals or enroll new members.
- Operator-signed, one-use lifecycle decisions suspend, reactivate and offboard
  a tenant. Suspension immediately denies membership checks. Offboarding is
  terminal in this contract.
- Credential metadata consists only of external vault references. Admins may
  enroll, replace or revoke a reference; old references are retained in a
  retirement ledger for external vault reconciliation. No raw key or secret is
  stored here. Vault-side destruction remains a separate required operation.
- The synthetic evidence store requires active membership on each read/write,
  a synthetic marker, bounded payload and a matching SHA-256. The signed
  onboarding approval fixes its retention period. Expired payloads and
  offboarded payloads are removed while digest-only audit records remain.
- A per-tenant encrypted backup covers current synthetic evidence and its
  audit records. The Fernet key remains outside the service and repository.
  Restore checks the same active tenant, current administrator, authentication,
  digest, retention period, record bounds and immutable audit consistency.

The backup has a bound of 32 current evidence records, 128 audit records and
1 MiB ciphertext. It is a synthetic control-plane drill, not a production
backup of connector credentials or the existing historical evidence store.
Fresh production readiness still needs a reviewed external secret vault,
vault-side rotation/revocation, durable trusted clock, full registry backup and
restore, legal retention approval, independently protected audit custody,
service supervision, full Linux suite and independent security review.
