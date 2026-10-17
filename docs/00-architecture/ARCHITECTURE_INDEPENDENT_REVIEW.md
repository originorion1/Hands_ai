# Architecture exact-head review packet, issue #236

Reviewer status: implementation-engineer evidence collection; **independent
security and architecture decisions are pending**. This file does not claim
independent approval. Canonical remains
`eabda90593f00ddc9b6ace6a4b9b9301fb3a01dd` (tree
`1cfb49df43247ddc94ca9bed5f876cb3b52cbfe1`). All connector PRs remain
draft and unmerged.

| PR | Head | Tree | Parent in stack |
|---|---|---|---|
| #219 | `819bfb2df9f75926bd0ca5f0d37eb6572e0ed868` | `3f68d4b6b82856ad9f145ea84b790de2c5979dd7` | canonical |
| #220 | `14a453c415eb354aec6c1677a22fabcff39c0fee` | `387a7a82882fc51d35c2b97d28581a0fc0dff83e` | #219 |
| #223 | `62e1a17519242c15de485e015ebec855978c3ad9` | `3000348cf8b1a50d93253dc3a6fc5f28bc724a92` | #220 |
| #224 | `c781fad6688def00089d8385d7bf7902d8ec3e69` | `ea2c6572ab01023beddbcc822ebfb502f775c390` | #223 |
| #225 | `d0d3920ba7633059681c991282075ecad2a7af3b` | `b7a800f7f47284a6a2fc4b3258f2205875950d96` | #224 |
| #235 | `1ae9baa696a14516a555b03ebeb00f8f4feb5ba5` | `e95ca19ba1630d4e42a8875d819819009a52dcac` | #225 |
| #240 | `be7901e823236e84457aacf7fc8ab6f155142ca4` | `fd7450b9aa147224fb9dd603a2772befecf7a553` | #235 |
| #241 | `6ff29141081acba6f2eae808b3b490d6c727e3cd` | `c8c1630388bb2425683f721ce7337f1ee1426368` | #240 |

Git `merge-base --is-ancestor` returned zero for every adjacent pair.
GitHub reported each PR open and draft, with the corresponding predecessor as
base. No unexplained ancestry gap was observed. Revalidation is required
after every human merge, because a new merge tree is a new candidate.

## Confirmed findings

| Severity | Source, reproduction, impact | Smallest correction and state |
|---|---|---|
| P0 integrity | At #235 `src/orion/kernel.py:36` appends observations individually. Injecting failure on the second append can leave partial evidence after a failed discovery. | Atomic batch port in #240; 51-line focused regression. Hosted Ubuntu run 38990739701 passed 2201 tests, 31 skipped, 64 subtests at the exact #240 head. Unmerged. |
| P0 integrity | At #235 `src/orion/stores/memory.py:14` retains mutable nested payloads and returns the same object at query. Mutation can alter accepted evidence without a new receipt. | Snapshot at commit and query in #241; 66-line focused regression. Hosted Ubuntu run 38990743145 passed 2205 tests, 31 skipped, 64 subtests at the exact #241 head. Unmerged. |
| P1 release gate | #235 has no independent exact-head security and artifact-custody approval. | Owner-appointed reviewers must inspect final integrated tree and sign a trusted exact-manifest decision. OPEN. |

These are confirmed code defects at #235, not architectural preferences. The
downstream corrections do not retroactively make the #235 tree releasable.
No additional critical/high finding was confirmed by this bounded review;
that statement is not a complete independent penetration test.

Focused current review: 55 package, connector registration, historical and
Kernel-boundary tests passed in a clean offline install. The final release
tree still needs its own full stable-Linux suite, source/capability scans,
vulnerability review, integration tests and independent security signoff.
Privacy-safe tracing remains a design option; no telemetry dependency was
added and no raw customer records were processed.
