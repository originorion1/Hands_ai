# Frozen study-selection failure root causes

The unchanged PR #280 benchmark has 12 cases: nine designed positives and three negative/insufficient cases. `evaluation/pr279_study_selection/cases.json` SHA-256 is `8689d66882147b5a0d6bc4dbcf6dc25899ed41c6d271502818dac9d3411ab80b`; labels SHA-256 is `17336b28143e2921c69bbe5ab9cea9c8fc77cd6a507e97916b55831e1683db7a`. The source-to-conclusion evidence for every case, including full record digests, is reproducible with `evaluation/pr280_study_transfer/analyze.py`. PR #281's post-seal analysis is retained in `/home/orion/evidence/orion-pr280-study-transfer/pr280-original-analysis.json`.

| Case | ORION selected | Designed positive | Fixed/random/heuristic positive hits | Classification | Evidence explanation |
| --- | --- | --- | --- | --- | --- |
| P01 | Catalog Price (P01-R2) | Supplier Bill | fixed, random | prioritization | Authorized target existed; selected record price equals reference |
| P02 | Purchase Bill (P02-R2) | Purchase Bill | heuristic | correct selection | Governed record supports USD 49 designed exposure |
| P03 | Contract Line (P03-R3) | Freight Charge | fixed, heuristic | prioritization | Selected exception requires abstention; positive was another authorized target |
| P04 | Invoice Match (P04-R1) | none | none | correct abstention | Missing reference price, no designed positive |
| M01 | Asset Registry (M01-R2) | Repair Ticket | fixed, random | prioritization | Selected record has no variance |
| M02 | Breakdown Event (M02-R2) | Breakdown Event | none | correct selection | Governed record supports USD 77 designed exposure |
| M03 | Asset Ledger (M03-R3) | Parts Use | fixed, heuristic | prioritization | Selected exception requires abstention |
| M04 | Callout Note (M04-R1) | none | none | correct abstention | Missing reference price |
| I01 | Count Entry (I01-R2) | Count Entry | heuristic | correct selection | Governed record supports USD 98 designed exposure |
| I02 | Batch Master (I02-R2) | Pick Event | fixed, heuristic | prioritization | Selected record has no variance |
| I03 | Adjustment (I03-R2) | Warehouse Bin | fixed | prioritization | Selected record has no variance |
| I04 | Location Register (I04-R1) | none | none | correct abstention | Missing reference price |

For the six misses, **discovery, question generation, candidate coverage, evidence acquisition, deterministic reasoning and stopping did not fail within this fixture**. The positive target was one of three authorized relationships; the chosen metadata and record were acquired with the same two-read budget as every baseline; the conclusion followed the selected record. The immediate failure is prioritization. Each root schema exposes one relationship field per candidate and no observed seed attribute correlated with the hidden positive. The three positions are observationally exchangeable for any selector that does not use target names or generator order as a shortcut. Thus the deeper root cause is **insufficient decision evidence**, not an arithmetic or permission bug.

Fixed finds 6/9 because six positives were placed at the first declared target. Its success is traceable but may be a fixture-order artifact; random finds 2/9 and the Table-first heuristic 5/9. The benchmark cannot establish general intelligence from these labels. PR #281's generic reference-count policy leaves the frozen result at 3/9 because all candidates tie. It resolves 91/258 versus 66/258 observed relationship fields on a separate 32-case synthetic transfer set, while fixed still finds more positives (7 versus 6). That validates structural coverage at equal cost, not useful finding superiority.

A next falsifiable experiment must supply a provenance-backed signal about expected answer informativeness *before* target selection, estimate it only from training/development outcomes, freeze the policy, and evaluate on independent organizations with the same evidence budget. If such signal is absent or uncalibrated, the selector should report an information deficit instead of claiming that any tie-break is intelligent.
