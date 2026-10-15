# ORION Command Center deployment boundary

Issue #211 prepares a Stage A application deployment from the reviewed
Command Center in PR #201. It does not authorize a pilot launch.

## Build and routing

Set the Vercel project's Git production branch to the human-approved integrated
branch. Set **Root Directory** to the repository root (`.`). The committed
`vercel.json` selects the **Other** framework preset, copies only the named
Command Center assets into `public/`, and uses Python file functions at
`/api/health` and `/api/v1/status`. The root rewrite serves the reviewed
`index.html`. Do not use the old `main` configuration's two Python services:
neither has a valid application entry point for this UI, and its catch-all route
does not select the Command Center.

Before exposing a preview or production URL, require Vercel Authentication for
**all deployments and domains**, including the production domain. Allow only
the intended project members. Verify a signed-out GET of `/` and
`/api/v1/status` is redirected or denied, then verify both routes in an
authenticated browser session. Vercel project protection is an external
setting; this repository cannot attest it or create a user identity.
Do not add a protection-bypass token to the browser, repository, URL, or
deployment logs. Keep UI and API on the same origin, with no CORS permission.

The Stage A status function reports only its own HTTP health and explicit
disconnects. `GET /api/v1/report` is not deployed: a Vercel Function has no
private, durable report binding, and the Command Center correctly displays
unavailability. The local `orion-report-ui` endpoint remains loopback-only.

## Persistent runtime for Stage B

The installed `orion-runtime`, supervised broker, event worker, audit and
evidence custody, and SQLite stores require a separately managed Linux host
with persistent owner-controlled directories, isolated namespaces, bounded
network policy, exact installed artifact identity, and private secret files.
Vercel Functions have request lifetimes and no suitable persistent filesystem
or namespace ownership for those controls. Do not copy the runtime database,
tenant binding, signing keys, source credentials, or aggregate reports into the
Vercel build or environment variables.

Stage B requires a separately reviewed, tenant-bound, authenticated read-only
report service and a durable host deployment profile before the UI can show
real completed aggregates. The HTTP boundary must reuse
`orion.presentation.report_interface` and its canonical report validator;
it must not expose raw evidence, tenant selection, file paths, arbitrary
report replacement, or any start/authorize/execute endpoint. No host service
is started by this change.

Stage C requires the independent live-access ledger, readiness evidence,
unspent exact-purpose grants, cleared recovery hold, and human approval. The
current `execution_allowed=false` and `LIVE_PILOT_READY=false` doctrine
remains in force.

## Activation evidence

Record exact deployed Git SHA and Vercel deployment ID, successful build,
authenticated and signed-out route checks, `GET /api/health` and
`GET /api/v1/status` bodies, UI request to the same-origin status route,
and a failed or unavailable report connection. Do not describe a static page,
health response, or completed aggregate as a live ERP pilot.
