# Bounded metadata planner integration and authorization gaps

Status: offline engineering candidate only. No customer system was contacted.

## Acquisition-path change

`deployment/customer/bounded_metadata.py` now calls the generic
`plan_authorized_discovery` after each successful, field-projected metadata
acquisition. The planner receives only observations produced from approved
fields, and its metadata target set is the exact signed-grant DocType scope.
The acquisition boundary checks the selected target against that scope again.
Record targets are empty, so this path cannot propose business-record reads.
The first target remains the grant-selected seed and is recorded separately;
it is not credited as an autonomous ORION choice.

Each run records a hash-bound decision sequence containing the candidate order,
selected structural question, alternatives, observed evidence digests,
allowlisted source fields, uncertainty, and stop reason. The report reader
revalidates decision and evidence digests, candidate scope, field provenance,
and selection-to-acquisition binding. A changed decision invalidates the
derived report. `execution_allowed` remains false and ERP writes remain zero.

Request, duration, response, and total projected-evidence limits remain
enforced by the approved grant/runtime. At the 25-attempt boundary the next
candidate is recorded as not acquired and the session fails closed. Decision
records have their own 64 KiB maximum and are not counted as acquired ERP
evidence bytes.

## Intelligence result

The selector is the existing deterministic structural planner. It orders
authorized unseen structural references; it has no calibrated information
value, learned policy, or economic objective. The new tests show that it
selects a permitted next target, records alternatives and provenance, and
abstains when the only apparent link came from an unapproved field. These are
functional integration results, not evidence of intelligence superiority.

The existing PR #279 benchmark and labels are unchanged. It invokes the same
`plan_authorized_discovery` selector used here, with the same authorized
structural-candidate ordering. A fresh replay on its 12 label-blind synthetic
cases gives 3 positives for ORION, 6 for first-field fixed, 5 for the
Table-first heuristic, and 2 for seeded random. Every method receives the same
24 governed reads (one metadata and one record read per case); false positives
are zero, and the outcome interpreter is shared. Recall is respectively
0.333, 0.667, 0.556, and 0.222. ORION's Wilson 95% recall interval is
0.121-0.646; the fixed baseline's is 0.354-0.879; the heuristic's is
0.267-0.811. The fixed baseline wins. The labels are designed synthetic, not
independently adjudicated. Analyst effort and economic value were not
measured. Incremental intelligence and business value remain NOT_PROVEN.

The held-out replay used unchanged inputs: cases SHA-256
`8689d66882147b5a0d6bc4dbcf6dc25899ed41c6d271502818dac9d3411ab80b`, labels
SHA-256 `17336b28143e2921c69bbe5ab9cea9c8fc77cd6a507e97916b55831e1683db7a`,
prediction artifact SHA-256
`6ebcf392b4683a22e4dd9f137f6aa5e66a1787ee7b053cfc3ad04329c53214fd`, and
score artifact SHA-256
`30707ab931d446588e8bffe545275d8c66310679a1ba73eed9c0878c654a9b55`.

## Authorization envelope: unresolved mandatory fields

All fields below are required before a customer grant may be issued. A blank,
placeholder, owner proposal, or synthetic value is not approval.

| Required field | Current state | Required owner/customer evidence |
| --- | --- | --- |
| Customer authority and consent | Pending | Fresh customer authorization for one metadata-only session |
| Canonical tenant slug | Pending | Owner-approved canonical value and verified membership mapping |
| ERP company identity | Pending | Exact company identifier verified through an approved method; never inferred from hostname |
| Tenant-to-company mapping | Pending | Reviewed mapping receipt binding the canonical tenant and external company reference |
| Connector/runtime identity | Candidate only | Exact installed package versions, source/tree, artifact hashes, and independent qualification |
| Credential references | Not provisioned/verified | Private references to credentials proven read-only; no credential values in the packet |
| Destination/TLS/network | Not reviewed for live use | Approved hostname, certificate identity, DNS/egress controls, redirect policy, and SSRF boundary |
| DocType/field allowlist | Not approved | Exact metadata-only entities and fields, including sensitive-category exclusions |
| UTC window | Unset | Explicit start time and expiry no longer than 30 minutes |
| Request ceiling | Proposed, not granted | At most 25 attempted metadata requests; request 26 must be denied |
| Response/evidence ceiling | Proposed, not granted | At most 64 KiB per response and 256 KiB total projected evidence |
| Evidence custody | Not approved | Private storage identity, access list, integrity procedure, retention and deletion receipt |
| Revocation/emergency stop | Not assigned | Named authorized revocation operator, tested immediate stop, and escalation route |
| Independent security review | Pending | Reviewer not involved in implementation; exact installed candidate review and signed decision |
| Owner approval | Pending | Fresh approval bound to the exact envelope and runtime digest |
| Customer approval | Pending | Customer approval bound to the same company, scope, limits, and window |

Until these items are complete and a one-use signed grant is separately
activated, customer access remains `NO_GO`. This document contains no
customer identifiers, endpoint, credential values, or active grant.
