# Stable Ubuntu exact-candidate qualification run

**Status: PREPARED — not authorized or executed.** Do not enable GitHub Actions
or use a hosted runner until the owner grants explicit authorization. The
current WSL2 environment is diagnostic only and cannot satisfy this gate.

## Candidate binding

Before starting, record the exact committed source SHA and tree, clean worktree
status, Ubuntu release, kernel, Python version, tool versions, and start time
(UTC). Build the declared runtime dependency closure from source, hash every
wheel and sdist, install only those artifacts into a clean isolated environment,
and record the installed distribution metadata and entry points. The test run
must consume the installed artifacts, not the checkout.

## Qualification sequence

1. Verify all source, wheel, and sdist hashes against the immutable manifest.
2. Verify package closure, no checkout or test-fixture imports, and the ERPNext
   connector entry point.
3. Run targeted installed-runtime cases for tenant/company binding, signed
   grant and replay denial, pre/during/post-read revocation, request 26 denial,
   30-minute expiry, response and evidence limits, transport destination/TLS,
   no writes/business reads, and study-selection provenance.
4. Run `py_compile`, Ruff, the full pytest suite, UI/API checks, recovery checks,
   demo (`execution_allowed=false`), `git diff --check`, and capability scans.
5. Preserve command logs, exit codes, artifact hashes, environment identity,
   request/evidence digest fixtures, selected study questions, errors, and final
   budget totals. Keep the run synthetic; do not contact ERPNext.
6. Any source or artifact change requires a new manifest and complete rerun.

Do not mark stable qualification passed until an authorized stable Ubuntu run
completes on the exact final candidate. This document grants no runner, Actions,
deployment, signing, or ERP authority.
