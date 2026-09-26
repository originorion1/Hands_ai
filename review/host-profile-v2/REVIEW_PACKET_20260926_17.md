# Host-profile v2 veth peer-fix: offline review packet

Feature branch: `codex/host-profile-v2-peer-fix-v17`, based on canonical
`laboratory/orion-v0.1` at `40c02b3a`.

## Exact artifact bindings

- Reviewed v16 executable and v16 manifest remain byte-identical to the
  predecessor bundle. The archived `apply-host-profile-v2-v16.py` and
  `test_apply_host_profile_v2_failures-v16.py` preserve the v16 file bindings.
- `REVIEW_MANIFEST_20260926_17.json` chains to the unchanged v16 SHA-256 and
  binds the successor script, active tests, exact disposable raw captures,
  capture tool, reconstructed malformed-case fixture, recovery policy, this
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
  index-only peers after the move. Four raw `ip -j -details link` outputs are
  kept byte-for-byte in `captured-veth/`, with SHA-256 metadata. The capture
  ran under `unshare --user --map-root-user --net`, with a nested network
  namespace for the moved peer. The capture script required loopback-only,
  empty IPv4/IPv6 route tables in both namespaces before creating the veth;
  no uplink, default route, customer traffic, or pilot-host link was used.
  `veth_ip_link_representative.json` remains explicitly reconstructed.
- Once namespace or veth creation is recorded, rollback retains the entire
  recorded dependency set, including namespace nftables filter, host NAT and
  Docker rules, resolver, unit and evidence files, and consumed-grant marker.
  It reports `ROLLBACK_INCOMPLETE` with sanitized cause and resource categories.
- The full-order failure injection covers pending and completed placements,
  late identity failure, and replacement after verification but before rollback.
  Its mocks prohibit nft, link, file, and directory deletion.
- Focused host-profile suite: 49 passed, 47 subtests passed. Active successor
  script and active focused test files pass Ruff. Core `src`/`tests`
  Ruff passes. The archived v16 script/failure test retain 26 pre-existing
  Ruff findings; they are unchanged predecessor evidence. Syntax checks pass.
  The core source-scan subset passed 146 tests, and the core demo reports
  `execution_allowed=false`.
- Repository-wide suite: `981 passed, 47 subtests passed` in 33m49s. This
  includes all active host-profile tests and the core repository tests.

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
