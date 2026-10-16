# Offline evidence and findings

Source: canonical `eabda90593f00ddc9b6ace6a4b9b9301fb3a01dd`, synthetic fixture SHA-256 `da848620c31ea06ad2b9ccf01df3db5576030f1367ba7c954c57c017ec525495`, evaluator SHA-256 `e25239dc0f279a6b33b732ac55c660ac23bb42cc490d44dd9ffd9f4d42b11a61`, result SHA-256 `757bc6103580157ceea378c738a65c41742be7bb26e14f02a67bc828d17280d4`. No customer identifiers, credentials, private retained records, or customer ERP traffic were used.

## Observed synthetic findings

| Window | ORION observation | Evidence and uncertainty |
| --- | --- | --- |
| Calibration | `cost_center` is a review candidate; 2 of 4 independent synthetic identities have missing values. | One metadata provenance reference and four latest record evidence references; relevant-evidence digest `f91f7ea5527ea8c90f3fa0db5a736546f9d0f4d36e58253e2ce875eae6828d19`. Applicability unknown; no business defect validated. |
| Held-out | `project` is a review candidate; 2 of 4 identities have missing values. | One metadata provenance reference and four latest record evidence references; relevant-evidence digest `0119b9bdb9dd8a1c419b0d2633433562c44b9b8059d44a3e9f4b4b64a8c4cb20`. Synthetic independent label says expected exception; this is a false-positive review ticket. |
| Held-out after correction | No candidate remains after a consecutive second batch supplies both missing project values. | `stale_candidate_invalidated_after_correction=true`; old candidate must not be reused as a current finding. |

The evaluator assigns deterministic UUIDs only to synthetic evidence, validates `HistoricalEvidenceBatch` contracts, and reads no network or external store. It confirms wrong-tenant metadata denial and duplicate-sequence denial. The ORION aggregate keeps `execution_allowed=false`, `recommendation_allowed=false`, `promotion_allowed=false`, and `prediction_validated=false` throughout. The baseline rule is implemented separately from ORION's missing-value helper.

These provenance hashes bind the synthetic investigator's selected evidence and metadata references, but they do not authenticate customer source records or establish legal custody. A live pilot would need a fresh grant, independently retained source digests, connector identity, tenant-scoped private storage, and analyst adjudication. No actual customer finding or realized saving is verified here.
