# Release-engineering integration packet, issue #266

This branch reconciles the draft release gate (#249), isolated report-host
recovery (#252), localhost TLS renewal preparation (#259), bounded report
transport (#265), active-source Ruff (#261) and the #248 baseline health
register (#257). The JSON/Markdown health pair is explicitly a dated
pre-integration #248 snapshot; it must not be used as the health decision for
this changed tree. The final source SHA/tree and artifact hashes belong in a
separate immutable operator-controlled manifest generated after this branch's
last commit. The release-gate script requires that exact clean checkout and
independent signatures before promotion.

Package versions are intentionally independent: Kernel, SDK and synthetic
connector remain `0.2.0rc1`; ERPNext connector/report host and pilot
compatibility package are `0.2.0rc3`. The unchanged v1 API wire contracts and
`execution_allowed=false` remain in force. Customer login/onboarding,
intelligence benchmark, customer value ledger and product research stay on
their separate draft lines and are not qualifications for this release.

The branch adds no active staging deployment. The isolated report-host drill
uses loopback and synthetic evidence; the local TLS renewal procedure was
tested in a private disposable directory, not installed in the existing
facade. The current staging certificate file on disk is expired, although
the running facade still presents a different valid certificate. Its
correction needs a separately authorized controlled staging change.

Open gates: exact-head full pytest on stable Linux, full package rebuild and
hash inventory, independent vulnerability/security/architecture review,
actual supervised TLS/auth/browser/recovery qualification, trusted external
approval records and the owner's integration decision. All PRs stay draft.
