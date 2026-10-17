# ORION release and pilot decision record

Decision baseline: canonical `eabda90593f00ddc9b6ace6a4b9b9301fb3a01dd`;
connector candidate PR #225 `d0d3920ba7633059681c991282075ecad2a7af3b`;
offline pilot PR #228 `2c7fe3ea34cbb2258f03854b2002884214a76d9e`.
Issue #229 addresses the public package-name collision; issue #230 expands the
synthetic intelligence benchmark; issue #227 is untrusted customer research;
issue #231 records REA-derived decisions. These branches are separate and
unmerged. Exact candidate SHAs and artifact hashes must be taken from their
PR records after each branch is frozen.

| Decision | Current verdict | Evidence and remaining gate |
|---|---|---|
| ARCHITECTURE_RELEASE | BLOCKED | Draft stack's package boundary has focused evidence, but integrated exact-head stable Linux full pytest and independent security review are still required. |
| PACKAGE_SUPPLY_CHAIN | BLOCKED | Issue #229's offline hash-locked candidate must pass final exact-artifact review and hosted qualification before release use. |
| INTELLIGENCE_SUPERIORITY | NOT_PROVEN | Expanded synthetic benchmark reports zero incremental validated ORION discoveries over conventional rules. Labels are design annotations, not customer adjudication. |
| ECONOMIC_VALUE | NOT_ESTABLISHED | No independently verified avoidable cost, recoverable amount, or realized saving. Potential exposure is only synthetic. |
| CUSTOMER_PILOT | NO_GO | Fresh scoped owner authorization, credential custody, permission review, baseline freeze, and readiness qualification have not occurred. |
| PRODUCTION_RELEASE | NO_GO | No qualified customer pilot, commercial outcome, or release approval. |

The primary combined status
`ORION_ARCHITECTURE_RELEASE_QUALIFIED_AND_INTELLIGENCE_EVALUATED` is not
declared. Synthetic staging functionality and green historical PR checks are
not production or customer authorization. Keep `execution_allowed=false` and
do not promote knowledge or execute recommendations. Human decisions still
needed: independent PR review and merge sequence, authorization for an
exact-head stable Linux runner if no other approved runner is available, and
fresh customer-specific pilot authorization only after engineering and
evaluation gates are met. No customer access is requested by this record.
