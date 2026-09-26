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
  inode, host and namespace placement inventories, reciprocal sysfs
  `ifindex`/`iflink` reads, and every reported peer reference to agree. A name
  or numeric `link_index` must be present for each endpoint. Missing,
  malformed, or conflicting fields fail closed.
- The successor records `veth_pending` before invoking one `ip link add` that
  creates H in the current namespace and P directly in the named namespace.
  It verifies both endpoints before promoting the journal entry and again
  before address configuration. There is no separate link move. A command
  error after possible creation leaves the intent recorded for the rollback
  hold. A replaced or uncertain namespace name fails verification and retains
  the dependency set.
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
- A second genuine disposable capture in `captured-veth-direct/` used direct
  cross-namespace creation. It binds raw detailed JSON for both endpoints,
  host and namespace listings before and after creation, and raw reciprocal
  sysfs text from each endpoint's own network and mount namespace. The
  endpoint ifindexes are both `2`, which is valid because each namespace has
  its own index space. The capture tool required empty route tables and
  loopback-only namespaces before creation, mounted sysfs read-only, and
  rejected any JSON/sysfs or placement mismatch. It did not use a pilot-host
  link, uplink, default route, or customer traffic.
- Once namespace or veth creation is recorded, rollback issues no deletion for
  the recorded dependency set, including namespace nftables filter, host NAT
  and Docker rules, resolver, unit and evidence files, and consumed-grant marker.
  It reports `ROLLBACK_INCOMPLETE` with a sanitized cause and
  `rollback_unresolved_categories`. These are journal kinds and failed proof
  steps, not an observation that the resources still exist. The legacy
  correction-receipt field `retained_v2_resource_names` has the same meaning;
  actual survival requires manual inspection.
- Full-order failure injection uses direct captured JSON and sysfs evidence
  for pending and completed journal states, late identity failure, command
  uncertainty, and replacement before rollback. Its mocks prohibit nft,
  link, file, and directory deletion. Caller tests exercise actual
  `create_veth` through `apply` and its rollback, including malformed
  listings, missing sysfs, peer drift, namespace drift, and a link replacement.
  Reconstructed fixtures remain for additional malformed input cases.
- Focused host-profile tests passed: 56 tests and 58 subtests. The full
  repository suite passed: 988 tests and 58 subtests in 1890.30 seconds.
  Python syntax checks, Ruff on the changed Python files plus `src` and
  `tests`, the demo (`execution_allowed=false`), and `git diff --check`
  passed. The changed capture and test files pass the source/capability scan.
  The generic scanner still flags the guarded host script under its Python
  network policy, as it did at the predecessor head; this is a pre-existing
  scanner finding, not a new finding from direct creation.

## Review findings carried forward from `40057b007d27d0d6cedf725068e28c4776bf4f2e`

1. **Captured link JSON lacked matching sysfs evidence in caller tests.**
   `capture_disposable_veth.py` now captures raw `ifindex`/`iflink` text in
   both placements and rejects disagreement with the link JSON. The changed
   `captured-veth/before_host.json`, `captured-veth/before_peer.json`,
   `captured-veth/after_host.json`, and `captured-veth/after_peer.json` link
   outputs; new `captured-veth/before_namespace_listing.json`,
   `captured-veth/after_namespace_listing.json`, and
   `captured-veth/sysfs_indexes.json`; and changed
   `captured-veth/capture_metadata.json` bind that evidence;
   `apply-host-profile-v2.py` checks the expanded capture set and hashes.
   `test_captured_veth_fixture.py::CapturedVethFixtureTests` checks the paired
   raw evidence and reader;
   `test_veth_peer_callers.py::VethCallerTests::test_create_veth_uses_raw_capture_and_reciprocal_sysfs_evidence`
   and `test_veth_peer_callers.py::VethCallerTests::test_apply_direct_creation_verifies_before_addressing`
   now exercise direct creation and verification through callers.
   `test_veth_acceptance.py::FullCreationRollbackAcceptance::test_identity_failure_retains_full_dependency_set_in_pending_and_completed_records`
   uses the direct capture in both full-order failure paths. The reconstructed
   fixture remains labeled as such.
2. **Rollback categories implied confirmed surviving resources.**
   `apply-host-profile-v2.py` emits `rollback_unresolved_categories` for
   recorded journal dependencies and documents the legacy correction-receipt
   field `retained_v2_resource_names`; `VETH_PEER_RECOVERY_HOLD.md` states
   that neither field proves present resources. The new
   `test_veth_peer_callers.py::VethCallerTests::test_rollback_categories_are_recorded_dependencies_without_presence_claim`
   asserts the categories without any live presence read or cleanup;
   `test_veth_acceptance.py::FullCreationRollbackAcceptance::test_identity_failure_retains_full_dependency_set_in_pending_and_completed_records`
   asserts the renamed report field in both journal states while deletion
   remains prohibited.

These findings appear addressed in the code and offline tests at `40057b0`
and remain covered by the current direct-creation tests.
This disposition is an author-side assessment, not independent human review
or maintainer approval.

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
