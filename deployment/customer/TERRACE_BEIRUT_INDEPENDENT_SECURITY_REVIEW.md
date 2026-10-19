# Independent security review packet — metadata-only pilot

**Status: PREPARED — review not performed.** Reviewer must not be an
implementation author. This packet contains review pointers only and does not
authorize customer or ERP access.

## Exact candidate material

Record at review time: source commit and tree; all wheel and sdist SHA-256
values; install transcript; qualified test log digests; and the approval-packet
digest. Any source or artifact change invalidates the review.

## Required review areas

- Signed grant binds company, tenant, destination, credential references,
  exact DocTypes and metadata fields, UTC window, and all budgets.
- Proposal cannot issue a grant; signatures and key identity are verified.
- Tenant and company mapping cannot be supplied or overridden by browser input.
- Credential resolution happens after one-use claim; secret values never enter
  logs, reports, exceptions, or browser output.
- HTTPS hostname verification, reviewed CA, no proxy, no redirects, fixed GET
  route, and reviewed egress/SSRF policy.
- At most 25 attempts and 30 minutes; response cap 64 KiB; projected evidence
  cap 256 KiB; over-limit and interrupted work fail closed.
- Revocation checks before and after each read and around evidence publication;
  restart/replay denied; emergency stop blocks access promptly.
- Only schema metadata is read; no record methods, business rows, writes, or
  production execution; `execution_allowed=false` and ERP writes remain zero.
- Private evidence custody, digests, retention deadline, and narrowly scoped
  deletion behavior.
- Study planner selects only unseen metadata targets within the signed grant;
  decision and evidence digests are bound in the installed report.

## Reviewer decision

- Reviewer identity and independence: pending
- Reviewed commit/tree and artifact-manifest digest: pending
- Findings and evidence references: pending
- Decision: **PENDING**
- Signed at (UTC): pending
- Signature: pending
