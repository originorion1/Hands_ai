# PR #209 follow-up review packet: fork termination and rollback test accuracy

Successor review work is based on PR #209 head `14f98b39ed40308159c3c98718d569935d70eee3` (tree `ea164fa7cad792a721d459f51317e01e7c013118`). The v17 source and review manifest are preserved byte-for-byte and bound as the predecessor. This follow-up has not been committed or pushed.

## Changes made for the two review findings

1. **Fork child termination.** In `veth_nsid.py`, the child branch wraps descriptor close, `setns`, the query, and pipe write in a `BaseException` guard. On any exceptional exit it calls `os._exit(1)`; on success it calls `os._exit(0)`. The child cannot fall through into the caller after an unexpected Python exception. `test_veth_nsid.py` injects `struct.error` and `RuntimeError`, confirms failure exit, and asserts no result was written.
2. **Rollback test semantics.** Rollback deliberately retains the complete recorded dependency set without presence inspection or deletion once any namespace/veth creation attempt is journaled. The dead per-veth and per-namespace cleanup branches were removed. Caller and failure-injection tests were renamed and simplified to assert this actual policy: recorded categories are returned, peer/namespace/sysfs identity inspection is not attempted, and no cleanup command is invoked. The tests do not claim that rollback identity verification runs.

## Evidence chain

`REVIEW_MANIFEST_20260926_18.json` chains to the unchanged v17 manifest. It binds the updated script, NSID helper, tests, this packet, and the archived v17 copies of changed active files. `FUTURE_GRANT_BINDINGS_20260927_18.json` contains digest bindings only; it creates no grant. Any future grant must name the exact v18 manifest and script digests. The previous grant remains spent.

## Verification status and limits

Fresh offline verification on this uncommitted tree: 52 focused `unittest`
cases passed across the host-script, failure-injection, NSID, caller, acceptance,
and capture-fixture modules. One unrelated receipt-schema test was not run
because `jsonschema` is unavailable in this environment. Python compilation,
the 47-file v18 manifest bindings (including the unchanged v17 predecessor
chain), and `git diff --check` passed. Ruff is unavailable here. The full
repository suite was not run on this follow-up; v17's previously reported full
suite is not evidence for these edits.

This is offline code work only. Host recovery remains on hold; no host command, deletion, retry, qualification, customer traffic, grant creation, or grant reuse is authorized or performed here. Independent human review and maintainer approval remain pending. The patch makes no atomic race-safety claim; future deletion requires externally enforced network-administrator serialization.
