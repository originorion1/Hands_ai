# Customer needs priority matrix (issue #227)

Source baseline: canonical `eabda90593f00ddc9b6ace6a4b9b9301fb3a01dd`.
Trust categories: **public market signal** suggests a segment problem;
**issue #227 hypothesis** proposes a product test; **customer interview** and
**customer-specific evidence** are absent. None of the first two categories
authorizes customer access or establishes purchase intent.

[NRA 2026](https://restaurant.org/research-and-media/media/press-releases/persistent-cost-increases-and-enduring-demand-will-shape-the-restaurant-industry-in-2026)
reports substantial US restaurant cost pressure and 42% of surveyed operators
unprofitable in 2025. [IFDA's 2025 survey](https://ifdaonline.org/research/aligning-distributor-strategies-with-operator-needs/)
reports 89% citing food costs and 3.1 distributors on average among 500 US
restaurant operators. These figures describe their samples, not ORION's market
or a specific customer. Issue #227 also cites Deloitte's restaurant AI survey
and an Informa market report; those are secondary research inputs.

| Rank | ICP, buyer and operator | Job and hypothesized problem | Current workaround or alternative to verify | Minimum evidence | Falsifiable acceptance metric |
|---|---|---|---|---|---|
| P0 | Multi-location food service; CFO/procurement buyer, site manager operator | Catch unit-normalized supplier price drift before invoice approval | ERP price report, spreadsheet, AP review | Contract price/version, UOM conversion, supplier, receipt, invoice, effective dates | Blindly reviewed incremental true findings over existing report; precision >=80%, complete provenance, analyst time and net cost measured |
| P0 | Same ICP; controller buyer, AP clerk operator | Find duplicate/incorrect invoices and credit mismatch | ERP duplicate controls, AP matching, supplier statement reconciliation | Invoice identity, line amounts, credits, payment state, receipt refs | Incremental verified exceptions after ERP controls, with recoverable amount separately adjudicated |
| P0 | Inventory-intensive distributor; operations buyer, stock controller operator | Reconcile purchase, receipt, movement and count gaps | Cycle counts, stock ledger report, spreadsheet | Receipt, movement, count timing, UOM, reversals, ownership | Validated gaps per 1,000 lines, false-positive rate, review minutes, and count-boundary coverage |
| P1 | Multi-location food service; CFO/operations | Explain margin deterioration without confusing promotions or missing cost data | BI dashboard, ERP gross-margin report, manual recipe review | Sales, recipe/version, purchase costs, promotion, waste, labor exclusions | Blind review finds at least one economically meaningful cause beyond existing report without overstating accounting coverage |
| P1 | Distributor or hospitality group; operations lead | Reduce cross-system exception triage | BI export, manual join, implementation partner | Scoped ERP/POS IDs, temporal joins, provenance and authorization | Median analyst minutes per verified finding lower than baseline at equal recall |
| P2 | Executive sponsor | Faster visibility and outcome feedback | Scheduled management pack, dashboard | Time-stamped prediction, independently verified outcome, reviewer disposition | Held-out improvement over non-learning baseline with no future leakage |

The first cohort is multi-location hospitality with procurement and inventory
volume; the second is inventory-intensive distributors. ERP partners are a
channel/design-partner hypothesis, not an end-customer substitute. Every
feature remains conditional on interviews and baseline evidence. A feature
should be cut or deferred when the existing ERP report matches its findings
at lower review cost. Connector-independent schemas and capability visibility
are cross-cutting requirements; ERPNext-specific assumptions cannot enter
Kernel intelligence rules.

## Questions for interviews and willingness to pay

Interview owner/CFO, procurement, AP, stock operations, and IT roles with
consent. Ask for the last independently verified exception, its discovery
path, time to review, cost recovered, false alarms, and actions taken. Ask
which ERP/POS report already covers it, what data is inaccessible, and which
missing evidence would block a conclusion. Test a blinded report against their
existing workflow. Ask what monthly spend, setup effort, and contract terms
they would accept **after** measured incremental value; do not treat stated
interest as a purchase commitment. Record negative interviews and refusal to
share data. Keep raw interview notes and customer-specific evidence in private,
consented custody, separate from public research and ORION knowledge.

No customer interviews or customer outcomes were performed for this document.
