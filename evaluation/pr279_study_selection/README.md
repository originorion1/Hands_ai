# PR #279 structural study selection trial

This offline trial asks one narrow question: given the same seed schema and one
authorized next question, which target does each method study? It uses 12
synthetic environments across procurement, maintenance, and inventory. Each
method receives the same tenant, source, seed fields, allowed targets, and
two-read budget. The four methods are the existing ORION structural planner,
first declared field, seeded random selection, and a Table-first heuristic.

`benchmark.py` does not load `labels.json`. It selects a target, passes one
metadata request and one record request through the governed discovery runner,
and seals evidence references and record digests in its predictions. The same
deterministic evidence interpreter is applied after selection for all four
methods. It emits a candidate only for a positive normalized price difference
without an approved exception; missing reference prices, unreviewed currency
or units, invalid numbers, and inconsistent digests cause abstention. The
financial number is synthetic estimated exposure, not verified savings.

After predictions are saved, `score.py` loads the separate designed labels.
The labels were authored for this synthetic trial; no independent domain
reviewer has adjudicated them. The cases are small, schema names can be
suggestive, and target ordering can affect the fixed baseline. This measures
question choice under these cases, not general business value. The selector
has no access to labels or later outcomes, and the conventional methods may
win.

Run from the repository root with the project installed:

```sh
PYTHONPATH=src:. python -m evaluation.pr279_study_selection.benchmark --output /tmp/study-predictions.json
PYTHONPATH=src:. python -m evaluation.pr279_study_selection.score --predictions /tmp/study-predictions.json --output /tmp/study-score.json
```

The sealed trial has nine designed positive cases. With 24 governed evidence
reads per method, first-field selected six positives, the Table-first heuristic
five, ORION three, and seeded random two. There were zero candidate false
positives; ORION abstained in five cases. ORION's 3/9 recall has a 95% Wilson
interval of 0.121–0.646. Its 3/3 candidate precision has a 95% Wilson interval
of 0.438–1.000. The fixed baseline wins this trial. Analyst time and economic
value were not measured, so incremental value remains unproven.

The reproducible next experiment is to freeze a larger, independently authored
set of unfamiliar schemas, pre-register one question and equal request budgets,
blind each method to labels, then adjudicate candidate usefulness and analyst
effort before scoring. A useful gain requires an independently validated
conclusion or material review improvement over the strongest baseline at
comparable access and total cost.

`browser_drill.py` is a separate, optional real-Chromium drill against an
in-process synthetic OIDC and portal transport. It requires Playwright and a
Chromium binary. Set `ORION_TEST_CHROMIUM_PATH` when the installed browser is
not at Playwright's default path. The in-process route transport may not follow
the cross-origin callback redirect in older Chromium builds; the harness then
completes the same signed code-flow server-side and transfers only session
cookies to the browser. This fallback is recorded as a browser transport
limitation and is not evidence of a real IdP deployment.
