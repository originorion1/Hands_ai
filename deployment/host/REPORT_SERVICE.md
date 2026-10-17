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
hosting settings after the host and identity issuer are qualified. For a
non-loopback backend URL, Vercel also requires `ORION_REPORT_SERVICE_TOKEN`.
Install the same independently generated 43-character base64url token in an
owner-only `0600` file on the report host and start it with
`--service-token-file /absolute/private/path`. The host reads this file on
every request, including `/ready`; missing, duplicate, and wrong service
credentials are denied. An unavailable credential file makes readiness and
report reads return 503. Rotate by atomically replacing the host file and
updating the server-side Vercel variable; the old value stops working as soon
as the host file changes. Keep both values out of logs and source control.
The local loopback deployment can continue without this external-service
credential. No private
paths, policy subjects, tokens, report contents, ERP credentials, or tenant IDs
belong in Vercel or the frontend build.

The Vercel `/api/health` route checks its own HTTP function. `/api/ready`
checks the separate report host with only the service credential and without a
report token or report body. A successful
readiness probe does not authorize a data request; the host verifies each
short-lived signed token and private report digest on every report read.

The governed ERP pilot, its persistent worker, audit journal, private SQLite,
and host isolation remain under their existing deployment profile and live
authorization gates. This report service imports no ERP connector and has no
write or execution route.
