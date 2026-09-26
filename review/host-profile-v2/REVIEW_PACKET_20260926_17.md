# Host-profile v2 veth peer-fix: offline review packet

Feature branch: `codex/host-profile-v2-peer-fix-v17`, based on canonical
`laboratory/orion-v0.1` at `40c02b3a`.

## Exact artifact bindings

- Reviewed v16 executable and v16 manifest remain byte-identical to the
  predecessor bundle. The archived `apply-host-profile-v2-v16.py` and
  `test_apply_host_profile_v2_failures-v16.py` preserve the v16 file bindings.
- `REVIEW_MANIFEST_20260926_17.json` chains to the unchanged v16 SHA-256 and
  binds the successor script, active tests, exact disposable raw link and
  sysfs captures, capture tool, reconstructed malformed-case fixture, recovery policy, this
  packet, and predecessor archives. Archived predecessor tests are excluded from current
  pytest collection by a manifest-bound `conftest.py`; their bytes are unchanged.
  Current v16 manifest SHA-256: `72275bb910fded1b48f03496b12326ee9d5d543d2a769d57d80666f9459de9b8`.
  Final v17 and script digests are recorded in
  `FUTURE_GRANT_BINDINGS_20260926_17.json` and checked against the committed tree.
- `FUTURE_GRANT_BINDINGS_20260926_17.json` records digest inputs only. It is
  **not a grant** and contains no owner approval or key material. The script's
  grant verifier requires the v17 manifest digest and exact successor script
  digest in any separately authorized future grant.

## Offline behavior and verification

- The pair check requires exact interface indexes, veth kind, stable namespace
  inode, reciprocal sysfs `ifindex`/`iflink` reads, and every reported peer
  reference to agree. A name or numeric `link_index` must be present for each
  endpoint. Missing, malformed, or conflicting fields fail closed.
- A genuine disposable capture found name-only peers before the move and
  index-only peers after the move. Four raw `ip -j -details link` outputs, two
  raw namespace listings, and raw sysfs `ifindex`/`iflink` text from each
  endpoint before and after the move are in `captured-veth/`, with SHA-256
  metadata. The capture ran under `unshare --user --map-root-user --mount --net`,
  with a nested network and mount namespace for the moved peer and read-only
  sysfs mounts. The capture tool rejected a prior isolated run whose inherited
  sysfs mount did not match link JSON; that output was not added to the bundle.
  The accepted run matched reciprocal sysfs and JSON indexes in both placements.
  The script required loopback-only, empty IPv4/IPv6 route tables in both
  namespaces before veth creation; no uplink, default route, customer traffic,
  or pilot-host link was used. `veth_ip_link_representative.json` remains
  explicitly reconstructed for malformed cases.
- Once namespace or veth creation is recorded, rollback issues no deletion for
  the recorded dependency set, including namespace nftables filter, host NAT
  and Docker rules, resolver, unit and evidence files, and consumed-grant marker.
  It reports `ROLLBACK_INCOMPLETE` with a sanitized cause and
  `rollback_unresolved_categories`. These are journal kinds and failed proof
  steps, not an observation that the resources still exist. The legacy
  correction-receipt field `retained_v2_resource_names` has the same meaning;
  actual survival requires manual inspection.
- The full-order failure injection now uses captured link and sysfs evidence
  for pending and completed placements, late identity failure, and replacement
  after verification but before rollback. Its mocks prohibit nft, link, file,
  and directory deletion. Caller tests also use the captured evidence for
  creation and post-move verification; reconstructed fixtures remain for
  malformed input cases.
- Focused host-profile tests passed: 53 tests and 51 subtests. The full
  repository suite passed: 985 tests and 51 subtests in 2123.35 seconds.
  Python syntax checks, Ruff on the changed Python files plus `src` and
  `tests`, the demo (`execution_allowed=false`), and `git diff --check`
  passed. The generic source/capability scanner flags the guarded host
  script under its Python network policy at both this tree and the unchanged
  predecessor head; changed capture and test files pass that scan. This is a
  pre-existing scanner finding, not a new finding from this follow-up.

## Open gates and recovery boundary

- The raw-capture evidence gate is satisfied for disposable development
  namespaces only. This does not verify current pilot-host state or authorize
  recovery.
- Human review and approval of this exact branch tree remain pending. No merge,
  integration, host application, recovery, or live-pilot readiness is implied.
- Host recovery remains on hold. The prior grant is spent and cannot be reused.
  Any future deletion needs an explicitly enforced maintenance window or
  equivalent serialization of all network administrators from identity read
  through deletion acknowledgment. This branch does not enforce one and does
  not claim atomic race safety; retain resources for manual recovery.

Independent review should check the final branch tree and manifest digests,
inspect the raw capture and verifier semantics, then obtain human approval of
that exact tree before any separate host authorization is considered.
