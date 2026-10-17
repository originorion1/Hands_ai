# Ruff applicability for the release candidate

At #248 `c631d1fe109f89360a3292784b262086ad39a4d6`, repository-wide
`ruff check .` reported 147 findings. JSON inventory showed 146 under
`review/host-profile-v2` and one in the active negative test
`tests/test_discovery_snapshot.py`. The review directory holds hash-bound
historical predecessor scripts and tests; editing them would alter retained
review evidence. Its Git tree before this patch was
`1b88df1bd17b49697bc8d2cae125676abbe0a903` and remains unchanged.

`pyproject.toml` now explicitly excludes only that archive from Ruff's active
source scope. The sole active finding is a deliberately naive datetime used
to prove rejection; it has a narrow `DTZ001` inline waiver. The negative
test still passes. `ruff check .` now passes for all active repository files,
and no production test or authorization rule was weakened. Pytest collection
is unchanged by Ruff's exclusion.

This lint result applies to this exact branch tree only. It does not replace
full stable-Linux pytest, independent security review or release approval.
