# Isolated report-host reliability candidate (issue #245)

The unit and environment files are templates for a new isolated installation.
They are not activated by this branch and do not modify existing canonical
staging. The release operator must fill the owner-controlled venv and private
paths, review the complete unit against host policy, and keep all listeners on
loopback behind an independently qualified TLS proxy.

`readiness_probe.py` reads the private service-credential file, calls only the
loopback `/ready` endpoint, emits a non-sensitive result and exits nonzero on
failure. An operator may attach its exit code to a monitored systemd timer or
other alerting system after reviewing that destination. No token or tenant
identifier is written to stdout.

`report_drill.py` runs the ERPNext connector/report-host `0.2.0rc3` against
Kernel and SDK `0.2.0rc1` in a private
temporary directory. It generates synthetic evidence and a separate ephemeral
issuer key, verifies service and report-token denials, subject revocation,
tamper detection, recovery after a forced process stop, persistent report
digest, and clean SIGTERM shutdown. It records local p95 report latency over
20 reads, crash-to-ready recovery time and data-loss bytes. These observations
are measurements, not service-level objectives or proof of production
capacity. TLS and browser checks remain separate exact-candidate gates.

The report host handles one accepted request at a time. Its listen backlog
is limited to 16, an idle accepted socket closes after two seconds, and an
absolute accepted-connection deadline closes it after five seconds even if a
peer trickles bytes. The server stays single-threaded, so a peer can consume
the sole worker for at most that interval. These are availability bounds,
not a throughput guarantee. A reviewed reverse proxy must enforce its own
connection limit before any public ingress is qualified.

Run only with a clean, hash-verified offline installation:

```sh
python deployment/release_candidate/report_drill.py \
  --manifest /private/release/DRAFT_MANIFEST.json
```

Do not use the public staging tunnel, customer ERP, production signing keys or
existing canonical service directories for this drill. Before any host
upgrade, capture a private backup and exact artifact manifest, stop only the
isolated candidate, install from the reviewed wheelhouse, run readiness/auth
checks, and retain the previous wheelhouse for rollback. A production disaster
recovery target requires owner-approved capacity and recovery objectives plus
an independently witnessed drill.
