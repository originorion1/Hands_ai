# Terrace Beirut procurement pilot: proposed authorization, NO_GO

The owner provided non-secret proposed identifiers for packet preparation only: customer **Terrace Beirut**, ERP family **ERPNext / ERPMAX**, company **`terrace-Beirut`**, tenant **`terrace-Beirut`**. No customer or owner authorization to access ERP data has been supplied. This packet contains no credential value or endpoint. The inherited detailed proposal is `../procurement_price_270/PILOT_AUTHORIZATION_PROPOSAL.md`.

## Exact owner and customer decisions still required

| Decision | Proposed minimum | Approval status |
| --- | --- | --- |
| Legal customer and company identity | Confirm the exact entity and ERP company key | Unapproved |
| Tenant ID | `terrace-Beirut` was proposed but violates the lowercase tenant registry grammar; owner must approve an exact alternative or an explicitly reviewed registry change | Unresolved; no silent normalization |
| Written consent | Owner and customer authorizations for the named read-only purpose | Missing |
| Connector | Reviewed ERPNext / ERPMAX package/version/entry point/hash, pinned destination and read-only enforcement drill | Missing |
| Credential reference | Named owner-approved vault reference and rotation/revocation owner, with no credential value in this packet | Missing |
| Entities and fields | Proposed Purchase Invoice/line, Purchase Order/line, Item, Supplier, contract price and FX evidence; exact allowlist of record ID, company, item, supplier, dates, quantity/UOM/pack, net/gross price, tax, discount, currency/FX, quality and delivery terms | Unapproved |
| Exclusions | People, payment credentials, free text, attachments and unrelated companies | Proposed; confirm with customer |
| Historical window | Proposed 90 days ending at authorized session start | Unapproved |
| Request/volume limits | Proposed maximum 200 GETs, 10,000 rows, 50 MiB | Unapproved |
| Session | Proposed one-use read grant, at most two hours, explicit UTC expiry | Unapproved |
| Retention/deletion | Proposed encrypted private custody with a 30-day deletion deadline and separately defined immutable audit retention | Unapproved |
| Stop controls | Revocation, emergency stop, permit checks on each request, no write methods and `execution_allowed=false` | Customer-connector drill missing |
| Human review | Named procurement analyst and independent finance reviewer; candidate cannot become verified saving without source-backed adjudication | Missing |

Before access, freeze the customer's current price reports, contract controls, supplier comparison rules, analyst time, anomaly backlog, and historical evaluation period. Give all benchmark arms the same approved evidence and period. The independent reviewer must separate observed price variance, avoidable cost, and realized savings and record actual review/request/compute costs. The customer baseline inventory itself requires lawful access and has not begun.

**CUSTOMER_PILOT=NO_GO.** No customer endpoint, data, credential or public staging action is authorized by this document.
