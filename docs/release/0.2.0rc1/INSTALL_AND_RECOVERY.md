# Offline installation, upgrade and recovery

Use a clean Python 3.12 virtual environment on a reviewed Linux host. The
release operator must obtain the exact source SHA/tree, SHA-256 manifest,
approved wheelhouse and separately custodied credentials before deployment.
Never resolve an `originorion-*` package from a public index by name.

1. Verify the release manifest and all wheel hashes outside the application
   checkout. Do not generate a replacement manifest from unreviewed files.
2. In a fresh environment, run
   `python scripts/verify_orion_wheelhouse.py WHEELHOUSE MANIFEST --install`.
   The verifier uses `--no-index`, `--require-hashes`, and `pip check`.
3. Install Kernel and SDK alone first and verify `import orion` succeeds while
   ERPNext modules and distributions remain absent. Install reviewed connector
   distributions separately, then verify their exact entry-point registration.
4. Keep report-host signing keys, service credentials, evidence and audit logs
   outside the checkout and wheelhouse. Bind staging listeners to loopback.
   Do not change the existing canonical staging services from this candidate.
5. Check health, genuine backend readiness, token denial/acceptance, TLS,
   tenant isolation, evidence digest and `execution_allowed=false` before a
   controlled synthetic restart. Preserve private evidence across restart.

For rollback, stop only the isolated candidate services, retain the candidate
artifacts and evidence as immutable audit material, restore the previous
reviewed wheelhouse and configuration, then repeat its health/readiness and
evidence-integrity checks. Never rewrite or delete historical evidence to
make the rollback appear healthy. Recovery time and data-loss exposure must be
measured in an isolated drill before release approval.

The release gate in draft #249 requires a clean exact source checkout, ten
verified package artifacts, hashed qualification records, and separate trusted
security, architecture and owner signatures. No such real approvals exist yet.
