# Host-profile v2 veth peer-fix: offline review packet

Feature branch: `codex/host-profile-v2-peer-fix-v17`, based on canonical
`laboratory/orion-v0.1` at `40c02b3a`.

## Exact artifact bindings

- Reviewed v16 executable and v16 manifest remain byte-identical to the
  predecessor bundle. The archived `apply-host-profile-v2-v16.py` and
  `test_apply_host_profile_v2_failures-v16.py` preserve the v16 file bindings.
- `REVIEW_MANIFEST_20260926_17.json` chains to the unchanged v16 SHA-256 and
  binds the successor script, active failure/caller/acceptance tests,
  reconstructed link fixture, recovery policy, and predecessor archives.
  Current v16 manifest SHA-256: `72275bb910fded1b48f03496b12326ee9d5d543d2a769d57d80666f9459de9b8`.
  Current v17 manifest SHA-256: `e96def9d55c7b875dedc53b0a44fe8bb441d2b731c0ac976252b4ae4393d9e38`.
  Successor script SHA-256: `21c074eb650ff2db55347e6e6c91d361711ffc86cda890e24054d136c3dc416d`.
- `FUTURE_GRANT_BINDINGS_20260926_17.json` records digest inputs only. It is
  **not a grant** and contains no owner approval or key material. The script's
  grant verifier requires the v17 manifest digest and exact successor script
  digest in any separately authorized future grant.

## Offline behavior and verification

- The pair check requires reciprocal peer names, exact interface indexes,
  veth kind, matching optional `link_index`, stable namespace inode, and
  reciprocal sysfs `ifindex`/`iflink` reads in each endpoint's placement.
- Once namespace or veth creation is recorded, rollback retains the entire
  recorded dependency set, including namespace nftables filter, host NAT and
  Docker rules, resolver, unit and evidence files, and consumed-grant marker.
  It reports `ROLLBACK_INCOMPLETE` with sanitized cause and resource categories.
- The full-order failure injection covers pending and completed placements,
  late identity failure, and replacement after verification but before rollback.
  Its mocks prohibit nft, link, file, and directory deletion.
- Focused host-profile suite: 47 passed, 41 subtests passed. Active successor
  script and all three active focused test files pass Ruff. Core `src`/`tests`
  Ruff passes. The archived v16 script/failure test retain 26 pre-existing
  Ruff findings; they are unchanged predecessor evidence. Syntax checks pass.
  The core source-scan subset passed 146 tests, and the core demo reports
  `execution_allowed=false`.
- The repository-wide 932-test suite was started offline but did not finish;
  it advanced only through roughly one quarter of cases over several minutes
  and was interrupted. This is an open verification gate, not a pass.

## Open gates and recovery boundary

- The full `ip -j -details link` fixture is **reconstructed**, based on the
  reported named-peer/no-`link_index` shape. No genuine disposable raw capture
  was available offline. The raw-capture evidence gate remains **UNRESOLVED**.
- Human review and approval of this exact branch tree remain pending. No merge,
  integration, host application, recovery, or live-pilot readiness is implied.
- Host recovery remains on hold. The prior grant is spent and cannot be reused.
  Any future deletion needs an explicitly enforced maintenance window or
  equivalent serialization of all network administrators from identity read
  through deletion acknowledgment. This branch does not enforce one and does
  not claim atomic race safety; retain resources for manual recovery.

Independent review should check the final branch tree and manifest digests,
obtain a genuine disposable raw capture for both peer placements, review any
new fixture and successor binding, then obtain human approval of that exact
tree before any separate host authorization is considered.
