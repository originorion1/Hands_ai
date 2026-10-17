# Loopback TLS renewal, offline preparation (issue #258)

Read-only diagnosis on 2026-10-17: the existing local facade is serving a
`localhost` certificate valid until 2026-11-15T17:45:23Z. Its on-disk
replacement file expired at 2026-10-17T00:15:23Z. The user timer only
restarts the proxy; it does not create a fresh certificate. The old renewal
helper imports a test fixture from a vanished `/tmp` worktree. A restart
could therefore load the expired file and break verified local HTTPS.
No staging files or services were changed during this diagnosis.

`renew_loopback_tls.py` is a versioned development-only replacement. It takes
an explicit absolute, owner-owned 0700 root. It creates a 30-day self-signed
RSA certificate with DNS `localhost` and IP `127.0.0.1` SANs, writes a 0600
key and certificate into a private version directory, verifies their public
keys match, fsyncs the files/directory, and atomically swaps one `current`
symlink to the complete pair. It does not install a trust anchor or restart a
service. Earlier versions remain for rollback. Tests cover successful TLS
hostname verification, wrong-host rejection, unsafe roots and interrupted
key write preserving the previous pair.

For a future **separately approved** staging correction, the operator should:

1. Back up the current on-disk certificate and key under private custody.
2. Use a durable private directory outside `/tmp`; create it 0700 and run
   `python -m deployment.release_candidate.renew_loopback_tls --root <private-dir>`.
3. Verify `current/cert.pem` validity and SANs, and test a separate isolated
   proxy with `curl --cacert current/cert.pem https://localhost:<port>/ready`.
   Verify that a wrong hostname fails. Do not add `--insecure`.
4. After independent review, configure the facade to read the `current` pair
   and perform one controlled restart. Recheck readiness, token denial and
   certificate fingerprint. If recovery fails, atomically restore the prior
   `current` link and restart once under operator supervision.
5. Only then install a reviewed renewal timer with an alert on failure and
   a restart step after a successful pair swap. Monitor expiry before the
   30-day bound. Do not couple this development certificate to ngrok or
   customer endpoints.

The observed live proxy certificate is not the file currently on disk.
The migration of the existing service to this versioned pair and any restart
require separate owner authority because staging must remain unchanged in
this mission. The script is not a public CA or production certificate system.
