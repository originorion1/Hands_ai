# Read-only report host boundary

This service is a separate deployment candidate. No host installation, worker
start, customer access, or production activation is performed by this branch.

Install the reviewed ORION wheel in a persistent host venv. Run the example
systemd unit under a dedicated `orion-report` user. The private environment
file supplies only absolute paths for `ORION_REPORT_BINDING_PATH` and
`ORION_REPORT_ACCESS_POLICY_PATH`, plus a loopback port. Keep the binding,
report, and policy in owner-only storage (`0600`, one hard link, no symlinks).
The report binding fixes one tenant, one report path, and its exact SHA-256.
The access policy fixes an independent Ed25519 issuer, audience, tenant, and
allowlisted subjects. Its `ed25519_public_key` is the report host's public
verification-key location; the file remains owner-only because it also binds
private tenant and subject values. The host loads it at startup and again for
every report read. A missing, malformed, or tenant-mismatched policy makes
`/ready` return 503 and report reads return 503 without disclosing the policy.
See `deployment/identity/REPORT_ISSUER.md` for the separate issuer contract.

The Python process binds only `127.0.0.1`. A separately managed HTTPS reverse
proxy may expose only `GET/HEAD /api/v1/report` and `GET/HEAD /ready`, forward
the `Authorization` header without logging it, set the upstream `Host` to
`127.0.0.1:<port>`, and deny all other paths and methods. TLS certificates and
the proxy are host operator responsibilities. Configure the Vercel project
variable `ORION_REPORT_BACKEND_URL` to the exact HTTPS report URL in its
hosting settings after the host and identity issuer are qualified. No private
paths, policy subjects, tokens, report contents, ERP credentials, or tenant IDs
belong in Vercel or the frontend build.

The Vercel `/api/health` route checks its own HTTP function. `/api/ready`
checks the separate report host without a token or report body. A successful
readiness probe does not authorize a data request; the host verifies each
short-lived signed token and private report digest on every report read.

The governed ERP pilot, its persistent worker, audit journal, private SQLite,
and host isolation remain under their existing deployment profile and live
authorization gates. This report service imports no ERP connector and has no
write or execution route.
