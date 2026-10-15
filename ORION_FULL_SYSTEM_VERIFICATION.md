# ORION full-system verification — 2026-10-15 to 2026-10-16

## Candidate and scope

- Canonical laboratory base: `374150b78ffd95fb55a72509c1a7ad20d0c0f7d8`.
- Reviewed Command Center predecessor: `8d11e2cbd66090f93ba5bd3d137bfec7a42b16d2` (PR #215).
- Integration implementation: `eb0053c920a6240dad7b6267fc46f6a3e9806909`, tree `cefd3e1845c50f0cac22c2b314901913e498c49f` (draft PR #216, issue #212).
- Verified correction candidate: `421ae652bd4ff11dc5b5cc64ab1313483f70290b`, tree `ff53206ff6e2c7fb4ac09a6caabb78fa226b0317`. It keeps the HTTP listener in an installed `orion_http` adapter outside ORION's restricted kernel package, pins the release-check actions to reviewed SHAs, and removes one obsolete Ruff suppression.
- No canonical ref was changed, no PR was merged, and no customer ERP request was made.

Code files changed from PR #215's head through `421ae65`:

```text
M .github/workflows/command-center-release-candidate.yml
M .gitignore
A api/ready.py
A deployment/host/REPORT_SERVICE.md
A deployment/host/orion-report.service.example
A deployment/local_preview.py
A deployment/vercel/readiness.py
M deployment/vercel/report.py
M deployment/vercel/status.py
M pyproject.toml
M src/orion/discovery/erpnext_live_session.py
A src/orion/presentation/__init__.py
A src/orion/presentation/report_auth.py
A src/orion/presentation/report_interface.py
M src/orion/presentation/static/2036.css
M src/orion/presentation/static/app.js
M src/orion/presentation/static/index.html
M src/orion/presentation/static/report-link.js
A src/orion/stores/private_files.py
A src/orion_http/__init__.py
A src/orion_http/report_server.py
A tests/system/browser_smoke.ps1
A tests/system/synthetic_browser.py
A tests/test_authenticated_report_server.py
M tests/test_discovery_snapshot.py
M tests/test_vercel_report_boundary.py
M tests/test_vercel_stage_a.py
M tests/ui/test-command-center.js
```

## Component inventory and architecture

| Component | Verified condition |
| --- | --- |
| Existing Command Center | Reused reviewed static assets from PR #215. The real browser rendered them locally. |
| Vercel HTTP handlers | Health, status, readiness, and versioned report routes are implemented as Python file-based functions. Local route tests passed. |
| Independent report host | Installed `orion_http` Python loopback service starts with a synthetic private report and verifies a short-lived Ed25519 token, subject allowlist, tenant, scope, and report digest on each data read. Tested locally; no persistent host installed. |
| Evidence and audit stores | Existing private SQLite stores and governed pilot modules remain in the Python backend. Synthetic persistence/restart and custody tests passed. No production storage was provisioned. |
| ERPNext connector and worker | Existing governed, read-only connector and host-isolated worker remain separate from Vercel. Offline readiness is blocked; no live connection or worker start occurred. |
| Identity and browser login | The UI accepts a short-lived independently issued report token in tab memory. No issuer or customer login service is configured, so real authenticated login is unavailable. |

The browser calls the same-origin Vercel `/api/v1/report` route. That route accepts only a bounded HTTPS report-host URL and forwards the authorization header. The local report host reads a digest-bound aggregate report and enforces its private tenant policy. The Vercel handler code holds no ERP credential, report file, tenant binding, or worker state. Observation, learning, execution, and knowledge promotion remain separate.

## Verification evidence

| Check | Result |
| --- | --- |
| Dependencies/startup | Isolated Python 3.12 venv: `pip install -e '.[dev]'` succeeded; `pip check` found no broken requirements. The built wheel includes `orion_http/report_server.py`. Synthetic report host and local application router started and served requests. |
| Python compilation | Changed Python modules compiled successfully. |
| Ruff | The first exact-head job stopped on one unused historical `noqa` directive in `tests/test_discovery_snapshot.py`. After removing that directive, `ruff check src/orion src/orion_http deployment api tests` passed. Whole-repository `ruff check .` still reports 146 findings in `review/host-profile-v2`; no unrelated review files were changed. |
| Full pytest | First full run on the integration tree: **2,201 passed, 6 failed, 64 subtests passed in 34:13**. Two failures exposed confirmed integration defects: a new raw HTTP import under `src/orion` and unpinned workflow actions. Both are corrected in `421ae65` and their existing regression tests pass. The other four first-run failures passed when rerun in isolation. Full rerun on clean correction commit `421ae65`: **2,204 passed, 3 failed, 64 subtests passed in 32:54**. The three failures are installed-runtime cases `erpnext_candidate`, `retention`, and `revoke`. The first two passed together when rerun alone; `revoke` failed in that group, then passed on a subsequent single-case run. The full suite is **not passing**. |
| Focused HTTP/security tests | 13 passed after the correction: signed access, tenant/scope/subject/signature denial, duplicate authorization rejection, digest tampering fail-closed, restart, HTTPS host path, local browser-to-host chain, kernel network-import scan, and workflow action-pin regression. |
| Existing persistence/custody tests | 60 passed, including SQLite reopen, tenant isolation, tampering, and custody checks. |
| UI/static build | Node Command Center interaction test passed. Static build passed. |
| Browser | Local headless Chrome on correction commit rendered the Command Center; empty and forged tokens showed no report; a valid synthetic token displayed the synthetic aggregate (3/7 metadata, 4 observations). Zero page exceptions and failed network requests were observed. These values are synthetic test data, not customer results. |
| Demo | Completed with `execution_allowed=false`. |
| Source/diff scans | No ERP/execution imports found in Vercel HTTP modules; `git diff --check` passed. |
| ERPNext readiness | Non-executing CLI returned `configuration_invalid`, `ready=false`, `execution_allowed=false` (exit 2). No ORION configuration variables were available in this shell. |

The actual local HTTP router also returned: `/` 200, `/api/health` 200, `/api/v1/status` 200, `/api/ready` 503 with `execution_allowed=false`, and unauthenticated `/api/v1/report` 401. The 503 is the correct disconnected state because no report host is configured. The local router and synthetic host were stopped after testing.

## Deployment evidence

- Vercel Preview for implementation commit `eb0053c`: `https://hands-2tigh6qoa-orionteam.vercel.app` (deployment ID `7102655422`; GitHub Vercel check succeeded).
- Vercel Preview for report head `e53cf36` containing the verified correction: `https://hands-nph0tbnox-orionteam.vercel.app` (deployment ID `7104259655`; GitHub Vercel check and deployment status succeeded).
- Direct unauthenticated requests to `/`, `/api/health`, `/api/ready`, and `/api/v1/report` on the newer preview each returned HTTP 302 to Vercel SSO. The public Command Center and deployed authenticated API therefore remain **unverified and unavailable** from the public internet.
- No production URL, persistent report host URL, identity issuer, or configured `ORION_REPORT_BACKEND_URL` is available. No production activation was performed.
- `gh run list --branch codex/connected-command-center-212` returned no GitHub Actions run at the inspection time. The Vercel deployment check does not replace the repository's test workflow.

## Blockers and decision

1. An owner-managed identity issuer, qualified persistent report host with TLS and private report/access policy, and Vercel backend URL configuration are needed for genuine authenticated reports. The repository contains a host unit and configuration instructions, but no running persistent service is claimed.
2. Vercel deployment protection redirects public requests to SSO. A protected preview build is not proof of a public working application.
3. Current ERPNext readiness lacks configuration/credential availability. A new read-only customer session also requires a fresh applicable authorization and field-level approval for its candidate scope. Existing consumed claims cannot be reused.
4. Installed-runtime scenarios intermittently fail closed on this WSL host. The exact full-run failures were `erpnext_candidate` (`packaged_deployment_witness_failed`), `retention` (`record_reasoning_denied`, supervised process blocked), and `revoke` (`record_reasoning_denied`, evidence inspection blocked). The underlying host or test timing cause is unresolved. Standalone successes do not replace the failed full-suite result, and no control or test expectation was weakened to make the gate pass.

**Application status: `PARTIALLY_OPERATIONAL`.** The offline integrated path works with synthetic evidence, while public authenticated access and persistent production services are disconnected. **Customer ERP live-pilot readiness: blocked and not exercised.**

## Final exact-head verification

Verified implementation commit: `421ae652bd4ff11dc5b5cc64ab1313483f70290b`, tree `ff53206ff6e2c7fb4ac09a6caabb78fa226b0317`. The code worktree was clean during the 2,204-pass / 3-fail full pytest rerun. `pip check`, compilation, scoped Ruff, focused regressions, local HTTP responses, browser smoke, Node UI checks, static build, demo, source scans, and `git diff --check` passed. The full pytest and whole-repository Ruff gates did not pass; production application activation is therefore not qualified. This report is added after the code-tree verification and does not change runtime code.
