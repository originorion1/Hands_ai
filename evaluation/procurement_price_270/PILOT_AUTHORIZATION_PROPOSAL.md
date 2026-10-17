# Controlled procurement pilot proposal — decision pending

This document is a proposal, not an authorization or a grant. The owner proposed **Terrace Beirut**, tenant ID **`terrace-Beirut`**, ERP system **ERPNext / ERPMAX**, and company **`terrace-Beirut`** for packet preparation only. No customer system has been contacted. The proposed tenant ID contains an uppercase `B`; the current offline tenant registry accepts lowercase IDs only, so the exact onboarding identity remains unresolved. Do not silently normalize it.

| Gate | Proposed bound or required owner input | Current status |
| --- | --- | --- |
| Customer, tenant, company | Terrace Beirut / `terrace-Beirut` / `terrace-Beirut`, proposed; approve exact legal and technical identifiers | **Proposed, not approved; tenant ID contract mismatch** |
| Customer consent | Written customer approval for named read-only scope | **Missing** |
| Connector | ERPNext / ERPMAX proposed; exact reviewed package, version, entry point, artifact hash and endpoint required | **Missing**; synthetic connector is evaluation-only |
| Credential reference | One vault reference with owner-approved destination; never a value in this packet | **Missing** |
| Entities | Propose Purchase Invoice/line, Purchase Order/line, Item, Supplier, contract price, exchange-rate evidence | Await field-level review |
| Fields | Record ID, company, item, supplier, date, quantity, UOM, unit/pack, gross/net price, tax, discount, currency, rate, grade and delivery terms; exclude names of people, free text, attachments and payment credentials | Await owner/customer review |
| Historical window | Propose 90 days ending at the approved session start, with no future records | Await approval |
| Request/volume cap | Propose at most 200 GETs, 10,000 rows and 50 MiB total evidence | Await capacity and minimization review |
| Session duration | Propose 2 hours maximum, one-use grant, explicit UTC expiry | Await approval |
| Custody/retention | Encrypted private evidence, hash manifest, 30-day proposed retention; preserve required audit records on deletion | Await policy review |
| Revocation/stop | Owner-revocable grant and credential reference, short-lived session, emergency stop before every request and on admission | Needs a customer-connector drill |
| Operations | GET only, exact company/date/field scope, zero ERP writes and `execution_allowed=false` | Must be verified against approved connector |
| Review | Named procurement analyst and independent financial reviewer; candidate only until both adjudicate | **Missing** |

## Customer baseline inventory, after authorization

Record the customer's existing purchase-price reports, contract checks, supplier comparisons, duplicate controls, analyst time, open anomaly backlog and current purchasing controls. Freeze the baseline rules and historical period before ORION reads the same scoped evidence. Have the independent financial reviewer verify avoidability against contract, quality, freight, tax, discounts and market context. Record observed cost avoidance separately from estimated exposure. Include review, request and compute costs in the net comparison.

## Stop conditions

Stop on expired or revoked grant, tenant/company mismatch, unreviewed destination, credential substitution, missing custody, request or byte cap, write-capable operation, reviewer withdrawal, or contradictory evidence. A technical prototype pass cannot change `CUSTOMER_PILOT=NO_GO` while any mandatory gate above is missing.
