# Study selection transfer evaluation

This is an offline, synthetic evaluation of structural question selection. It
does not measure customer outcomes or authorize ERP access. The PR #280
benchmark remains byte-for-byte frozen: `cases.json` SHA-256
`8689d66882147b5a0d6bc4dbcf6dc25899ed41c6d271502818dac9d3411ab80b`
and `labels.json` SHA-256
`17336b28143e2921c69bbe5ab9cea9c8fc77cd6a507e97916b55831e1683db7a`.
That set has **12 cases: nine designed positives and three negatives**.

## Root cause on the frozen set

Each root schema has three different authorized relationships, one field per
target. No seed feature distinguishes the positive target. The original
planner chooses the alphabetically first target. All six misses are at
**prioritization**: discovery and candidate generation found the allowed
relationships, both governed acquisitions completed, and deterministic
reasoning correctly handled the selected record. The selector cannot recover
the hidden positive by changing only a tie-break. The first-declared-target
baseline wins six of nine positives because the designed labels are often in
the first target; that is not evidence of a portable selection rule.

| Case | Designed positive | ORION target and observed source record | Result | Baseline positive hits | Failure stage |
| --- | --- | --- | --- | --- | --- |
| P01 | Supplier Bill | Catalog Price, P01-R2 | no anomaly | fixed, random | prioritization |
| P02 | Purchase Bill | Purchase Bill, P02-R2 | candidate | heuristic | none |
| P03 | Freight Charge | Contract Line, P03-R3 | abstained on exception | fixed, heuristic | prioritization |
| P04 | none | Invoice Match, P04-R1 | abstained on missing reference | none | none |
| M01 | Repair Ticket | Asset Registry, M01-R2 | no anomaly | fixed, random | prioritization |
| M02 | Breakdown Event | Breakdown Event, M02-R2 | candidate | none | none |
| M03 | Parts Use | Asset Ledger, M03-R3 | abstained on exception | fixed, heuristic | prioritization |
| M04 | none | Callout Note, M04-R1 | abstained on missing reference | none | none |
| I01 | Count Entry | Count Entry, I01-R2 | candidate | heuristic | none |
| I02 | Pick Event | Batch Master, I02-R2 | no anomaly | fixed, heuristic | prioritization |
| I03 | Warehouse Bin | Adjustment, I03-R2 | no anomaly | fixed | prioritization |
| I04 | none | Location Register, I04-R1 | abstained on missing reference | none | none |

The full source digests and per-method selections are produced by `analyze.py`
from the sealed predictions and source cases. Baseline successes use the same
two governed reads, not privileged data access. No finding is independently
adjudicated.

## Narrow selector change

A single authorized metadata read can resolve several already observed
relationship fields pointing at one target. The planner now ranks targets by
the number of those fields, with names used only to break ties. It retains
tenant binding, allowlists, provenance, the metadata-before-record boundary,
and the existing maximum target count. This optimizes **structural coverage**;
it does not treat connectivity as evidence of an operational anomaly.

## Separate transfer set

`generate.py` creates 32 cases in utilities, logistics, facilities, and
subscriptions with unfamiliar target names and varied relationship counts.
Graph shape and positive location come from independent seeded random streams.
Each case has four allowed targets. Twenty-four have a designed positive;
eight do not. The selector receives only the seed fields and authorization,
never labels or unselected records. Fixture hashes are pinned in the tests.
This generator is separate from the PR #280 benchmark, but it is still our
synthetic design and has no external reviewer.

Every method gets **one metadata and one record read per case**, through the
same governed runner. `analyze.py` checks tenant/source identity, selected
target allowlisting, source record reference and digest. Its structural
information-gain proxy is `relationship fields resolved / relationship fields
observed`. This proxy measures coverage of known structure, not Shannon
information gain about anomaly labels. Request count is the measured evidence
cost; analyst time and compute cost were not instrumented.

| Method | Frozen positives found / 9 | Transfer positives found / 24 | Transfer precision | Transfer structural coverage | Transfer requests | Transfer false positives |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| fixed | 6 | 7 | 7/7 | 67/258 | 64 | 0 |
| random | 2 | 5 | 5/5 | 63/258 | 64 | 0 |
| heuristic | 5 | 4 | 4/4 | 70/258 | 64 | 0 |
| ORION before | 3 | 3 | 3/3 | 66/258 | 64 | 0 |
| ORION after | 3 | 6 | 6/6 | 91/258 | 64 | 0 |

The transfer recall for ORION after the change is 6/24 = 25%, with a
Wilson 95% interval of 12.0%–44.9%. Fixed reaches 7/24 = 29.2%.
ORION gains four designed positive hits and loses one relative to its old
ordering. These paired differences are too few to show a reliable anomaly
detection improvement. The deterministic structural coverage increase is
25/258 fields at the same request budget. Original negative cases have three
justified abstentions; the transfer set after the change has zero abstentions
and eight valid no-anomaly conclusions on negatives. This is a test of
correct handling, not a claim that abstention should occur on every negative.

**Decision:** structural question coverage improved; incremental anomaly
detection and economic value remain **NOT_PROVEN**. A falsifiable next
experiment is to collect independently authored, blinded organization schemas
with an equal authorization budget, freeze them before choosing a planner,
then test whether structural coverage predicts useful findings. A finance or
procurement reviewer must adjudicate any claimed operational value without
seeing the selector or baseline conclusion first.

## Reproduce

With the project installed or `PYTHONPATH=src:.`, regenerate the transfer
fixtures with `python3 -m evaluation.pr280_study_transfer.generate`, run
`python3 -m evaluation.pr279_study_selection.benchmark --cases
evaluation/pr280_study_transfer/cases.json --output predictions.json`, then
run `python3 -m evaluation.pr280_study_transfer.analyze --cases
evaluation/pr280_study_transfer/cases.json --labels
evaluation/pr280_study_transfer/labels.json --predictions predictions.json
--output analysis.json`. The analysis step is post-seal and reads the labels.
