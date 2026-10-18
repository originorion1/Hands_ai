# Issue #284 customer/pilot dependency reconciliation

Refreshed GitHub and origin refs on 2026-10-18. Canonical
`laboratory/orion-v0.1` remains `4f9219b51ecb87e38be5a287f8a3822fa12a0a4b`.
The isolated customer candidate starts at PR #280 head
`1c05f5b3b97a08eead7a55c6b21e42195f1347f0` (tree
`8d102f72be3a2d2f5516db736128582abf8200f9`). Actual Git ancestor
checks passed for the complete chain below; the corresponding PRs are all
open drafts and are not individually merged.

| Sequence | Head | Principal changed ownership | Reconciliation |
| --- | --- | --- | --- |
| #269 | `d9c706cf` | Offline customer controls and synthetic lineage | Contained in #280 |
| #271 | `0d3be611` | Mid-read session revocation | Contained in #280 |
| #272 | `dfdb1ac4` | Procurement finding and custody | Contained in #280 |
| #274 | `29bfb6a8` | Generic report-host extraction | Contained in #280 |
| #276 | `4bf3ca33` | Procurement benchmark and report | Contained in #280 |
| #277 | `1b1bdcb3` | Bounded 25-attempt/30-minute metadata and customer-runtime package | Contained in #280 |
| #278 | `9140211f` | Multi-user portal, invitations and review-only proposals | Contained in #280 |
| #279 | `17552493` | Signed metadata approval, OIDC token exchange, portal report binding and procurement package | Contained in #280 |
| #280 | `1c05f5b3` | Tenant/report identity fixes and nine-case study evaluation | Base for this candidate |

Earlier #265 and merged #267 heads are Git ancestors of #280. #250, #251 and
#263 are **not** exact ancestors, although the candidate contains the catalog,
tenant lifecycle and OIDC files. Their distinct branch histories must not be
treated as merge instructions; no blind cherry-pick is planned. Research #281,
#282 and #283 are later descendants of #280 and remain separate. #283's
adaptive hybrid did not beat the strong heuristic on its new synthetic
evaluation, so this customer candidate does not include that policy.

## Current customer candidate changes

- `deployment/customer/metadata_transport.py`: one reviewed HTTPS origin,
  direct egress with no proxy, redirects denied before a second request,
  system or explicitly reviewed CA verification and GET-only metadata path.
- `deployment/customer/metadata_connection.py`: qualified installed launch
  constructs that transport from the signed enrolled grant.
- `deployment/customer/bounded_metadata.py` and `metadata_approval.py`:
  approved retention bound, persisted deletion deadline and guarded purge of
  only the grant's private state.
- `deployment/customer/metadata_study_report.py`, portal service and UI:
  immutable, custody-checked structural report derived from the same acquired
  metadata files; one permitted link becomes a review-only next question, with
  abstention if no link is observed. Evidence mutation invalidates the view.
- `packages/customer-runtime` and `packages/procurement-prototype`:
  packaging and dependency versions move together; no package publication.
- New focused synthetic TLS, retention and boundary regressions; exact-head
  full regression and installed artifact verification are required before
  qualification.

## Remaining integration gates

Real IdP/vault provider provisioning, exact ERP company mapping, customer
DocType/field approval, owner and customer grant signatures, destination/DNS
egress review, independent security review and stable Ubuntu runner are
external. The current synthetic procurement report and metadata preflight are
separate acquisition branches even when they share a tenant/session. The
metadata report closes same-source structural lineage only; it cannot support
a procurement price conclusion from metadata alone. The Terrace Beirut
packet records every unresolved field as unapproved. No customer access or
public staging change is implied by this branch.
