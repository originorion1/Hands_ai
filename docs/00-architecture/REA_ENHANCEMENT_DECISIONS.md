# REA enhancement decisions (issue #231)

Research baseline: isolated `rea-agents@6.5.0`, official release commit
`3b79af28e80785afce9f5998d8f03289402a1847`; tarball SHA-256
`531717a06e92ca651aab52691190f916fac044272c01f875e22c5e293218e699`.
The completed isolated assessment is retained at
`/home/orion/research/rea-6.5.0-20261017/REA_ASSESSMENT.md`. The
[official release](https://github.com/morluto/rea/releases/tag/rea-agents-6.5.0)
describes retained evidence and explicit unknown coverage. No REA code or
customer output is copied into ORION; no REA dependency or MCP registration is
introduced. Its [security policy](https://github.com/morluto/rea/blob/rea-agents-6.5.0/SECURITY.md)
also makes clear that local bridges are not a sandbox against same-user code.

| Practice | ORION before | Decision and measurable after | Authorization impact |
|---|---|---|---|
| Coverage status | Retained investigation records evidence counts but not complete-source proof. | Adopt explicit scope, missing-source and limitation fields in benchmark reports now; a runtime report schema needs a separate reviewed issue. A missing finding must never imply a complete search. | None. |
| Source provenance | Evidence IDs and SHA digests already exist. | Retain; benchmark records input fixture hashes and finding digests. Test every cited finding has a source reference. | None. |
| Investigation limitations | F&B assessment already states unknown ledger completeness. | Adopt explicit unsupported-class and unmeasured-metric declarations in benchmark; avoid confidence language for unsupported tasks. | None. |
| Confidence calibration | Current support fraction expresses semantic witness agreement, not outcome probability. | Defer calibrated probability until enough independent outcomes exist; benchmark reports empirical precision only. | None. |
| Blinded analyst review | Offline synthetic design labels are not blind adjudication. | Require blind reviewer labels and baseline comparison before value claim; no claimed after-result yet. | Separate customer authorization required for customer evidence. |
| Capability visibility | Versioned connector registry gives metadata without authority. | Defer a user-facing catalog until runtime availability and bounds are independently tested. Preserve `execution_allowed=false` for registration. | No new authority. |
| Package integrity | Name-only `orion-core==0.1.0` can resolve an unrelated public package. | Adopt issue #229's renamed distributions and reviewed offline wheel hashes; negative substitution tests are the benefit measure. | None. |

The [REA MCP contract](https://github.com/morluto/rea/blob/rea-agents-6.5.0/docs/mcp-contracts.md)
inspired evidence and capability presentation only. ORION should not expose
REA's analysis tools to customer connectors or use its tooling to cross an
authorization boundary. REA's demonstrated synthetic JavaScript analysis
does not prove ORION business intelligence or market demand.
