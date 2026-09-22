# Scoped verification lanes

`test_lanes.json` is a declarative selection map over existing pytest tests. It
does not register markers, alter pytest configuration, add dependencies, or
change default collection. A scoped lane is evidence for its named boundary,
never evidence that all repository tests passed.

Use the listed selections after the repository's existing pytest prefix:

```bash
.venv/bin/python -m pytest -q --durations=20 <selection...>
```

Use `--collect-only -q` with the same selection for a dry-run inventory. The
committed selections are:

- **contract:** the ten explicit nodes in `test_lanes.json` covering protocol
  freeze, blocked status, schema, answer rejection, authorship/review binding,
  release gating, and engine drift;
- **learning:** the existing prediction-ledger, event-outcome and organizational
  cycle files plus explicit bridge UNKNOWN and fresh-process recovery nodes;
- **installed:** `tests/test_packaged_runtime.py`, preserving its single
  module-scoped `clean_artifact` wheel build and running only on a capable host;
- **full:** no selection at all: `.venv/bin/python -m pytest -q --durations=20`;
  and
- **legacy validation:** the standalone V1 preflight command in the manifest,
  never execution, pytest, default CI, or an automatic rerun. Supported
  independent execution requires explicit conversion to V2.

Selections are intentionally written as explicit files or node IDs rather than
new markers. To execute a lane, copy its `selection` array as ordinary pytest
arguments. The manifest is data, not an authority to execute arbitrary values.

## Change-to-evidence map

| Change | Minimum focused evidence | Escalation |
| --- | --- | --- |
| Documentation only | Validate links, JSON, examples, hashes, and diff; reuse unchanged runtime evidence. | Run broader checks only when repository policy mandates them or the docs reveal behavior risk. |
| Evaluation tooling | Contract lane plus affected learning nodes. | Full lane for final code-changing tree. |
| Ledger, semantic, or admission | Corresponding ledger/event/normalization/UNKNOWN/recovery invariants. | Add neighboring affected files; use full for unknown/shared impact. |
| Deployment, custody, or transport | Affected installed scenarios on the approved capable host after checking builder/native prerequisites. | Final qualification may require the full installed module; do not substitute a weaker host. |
| Unknown or shared dependency impact | Full lane. | Never infer safe omission from paths alone. |

The map advises selection; it cannot certify completeness. Original failures
remain evidence. Correct a concrete defect, rerun affected checks, and repeat a
broader campaign only if the correction invalidated it or a required gate says
so. Do not run kernel cases concurrently unless their resource independence is
already established. Record `--durations` during ordinary selected/full runs
rather than launching a separate benchmark campaign.

The default local full command remains unfiltered, and GitHub CI remains
`python -m pytest -q`; both collect the whole repository. No lane changes
default selection, adds skip conditions, or weakens assertions. Required
compilation, Ruff, demo, capability/source scans, and diff checks remain part
of the one final campaign specified by `AGENTS.md`.
