# Business value scorecard

Evidence level: **synthetic regression only** at canonical `eabda90593f00ddc9b6ace6a4b9b9301fb3a01dd`. The four evaluation units are period-field pairs, not customers, invoices with verified defects, or realized outcomes.

| Question | Offline result | Customer conclusion |
| --- | --- | --- |
| Incremental validated discovery | ORION found **0** synthetic review-positive units absent from the conventional baseline. | No incremental customer value established. |
| Precision / false-positive rate | ORION first-candidate precision `1/2`, FPR `1/3`; baseline precision `1/3`, FPR `2/3`. | Too few synthetic units for a product-quality claim; held-out ORION candidate is a false-positive review ticket. |
| Review volume | Baseline emitted 3 candidate units; ORION emitted 2. | One fewer synthetic ticket, with no timed analyst study. At an **illustrative** five minutes per ticket, this would be 15 versus 10 minutes; those minutes are an assumption, not measured savings. |
| Verified financial impact | No customer outcome documents or independent counterfactual review. | **Unknown.** Do not record zero as a measured amount or convert missing fields to money. |
| Estimated exposure | No approved price, stock valuation, or causal attribution inputs. | **Not estimated.** |
| Learning across periods | No procurement/inventory model revision or prospective outcome resolution. | Improvement **not measured**. The current composed outcome-learning path concerns sales. |
| Inventory intelligence | No Stock Ledger Entry record evidence or reviewed inventory method. | **Not evaluated.** |
| Safety | Wrong-tenant and duplicate-sequence inputs denied; stale synthetic candidate removed after correction; `execution_allowed=false`. | Supports only the offline synthetic control checks, not live readiness. |

The go/no-go thresholds are preregistered in `PILOT_HYPOTHESIS.md`. On the available evidence, H1 has no incremental discovery, H2 lacks minimum independent adjudication and its synthetic held-out candidate is false-positive, H3 has no realized-value evidence, and H4 is untested. The business hypothesis is **unproven**, not validated by a working dashboard or connector.
