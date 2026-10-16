# Proposed first read-only pilot authorization

**Status: proposal, not a grant. No customer access is authorized.** Canonical source `eabda90593f00ddc9b6ace6a4b9b9301fb3a01dd`. Owner must fill the exact tenant and company through the private authorization workflow; neither is available in this offline work. The proposal cannot be signed or used with placeholders.

| Bound | Proposed exact scope for owner review |
| --- | --- |
| Tenant / company | `OWNER_PRIVATE_TENANT_ID_REQUIRED` / `OWNER_PRIVATE_COMPANY_ID_REQUIRED`; exact strings must match the reviewed connector grant, metadata, records, and evidence store. |
| Connector | Independently installed reviewed ERPNext connector wheel hash and contract version recorded in the grant. No implicit registration authority. Confirm read-only ERP role and destination before issuance. |
| Metadata entities | `Purchase Invoice`, `Stock Ledger Entry` only; at most **2** metadata requests total. Names and schema must be confirmed by reviewed metadata before record access. |
| Record entity, first session | `Purchase Invoice` only; fields `name`, `company`, `docstatus`, `posting_date`, `cost_center`, `project`. No child tables, attachments, free text, supplier/person identity, payment details, tax identifiers, prices, or credentials. |
| Inventory record entity | **None in first session.** A separate amendment may propose `Stock Ledger Entry` fields `name`, `company`, `posting_date`, `item_code`, `warehouse`, `actual_qty` after metadata and inventory analysis methods are reviewed. No inventory record read is authorized by this proposal. |
| Historical window | `2026-01-01T00:00:00Z` through `2026-09-30T23:59:59Z` inclusive, with calibration and held-out windows as preregistered. Owner must confirm data coverage and timezone semantics. No request outside the window. |
| Study limits | At most **4** Purchase Invoice record-study requests; at most **25** records per request and **100** records cumulatively. At most **2** study cycles. Stop at first limit. No pagination beyond the bound. |
| Credential reference | `OWNER_PRIVATE_READ_ONLY_CREDENTIAL_REFERENCE_REQUIRED`. Store only the reference in the manifest. The credential value remains in owner-controlled private storage outside the repository and report service. |
| Session | One supervised session, maximum **30 minutes** from grant issuance, with absolute expiry written into the signed grant. No renewal or retry by inference. |
| Allowed operations | Bounded metadata GET and record GET through the reviewed read-only connector, evidence verification, offline analysis, and private reporting. No ERP POST, PUT, PATCH, DELETE, database writes, action execution, or customer network access outside the exact destination. |
| Evidence custody | Private tenant-scoped encrypted or otherwise protected storage with append-only audit, source digest, connector artifact identity, request budget, timestamp, and grant identity. Raw extracts retained at most **30 days** after owner review, then owner-supervised deletion with a signed deletion receipt; aggregate anonymized metrics at most **90 days**. Any legal hold requires separate owner approval. |
| Revocation and emergency stop | Owner can revoke the grant immediately. The supervisor must cut network capability, stop acquisition, preserve audit/evidence state, and deny restart without a fresh grant. Clock rollback or custody uncertainty fails closed. |

## Issuance gates

1. Owner supplies private exact tenant/company and confirms a customer legal basis, the existing ERP report baseline, the schema, and the permitted historical window.
2. Review exact connector wheel SHA-256, SDK contract version, package dependency graph, credential reference, destination, TLS policy, read-only ERP permissions, private evidence path, and request budget.
3. Complete independent security/readiness review and obtain a fresh purpose-bound grant for this one session. Historical approvals or retained evidence do not authorize a new read.
4. Verify private identity issuer, revocation, key rotation, clock, audit ledger, and emergency stop. Fail closed on any mismatch or unsupported capability.

The inventory record amendment, any prediction deployment, recommendation execution, or knowledge promotion each require separate authorization. This proposal issues no production signing key or customer grant.
