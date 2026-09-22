# Feature Catalog by Module (Frozen)

Interlinks are marked ⟶.

## Masters (domains/masters)
Organizations, sites, plants, warehouses (with zones/locations), users/roles/permissions,
products & drugs (GTIN, schedules, storage conditions, is_prescription, is_controlled),
customers, units of measure, currencies, tax codes, departments, equipment registry,
specifications master link ⟶QC, BOM/master formula ⟶production, price lists ⟶sales.

## CRM (domains/crm)
Markets, campaigns, leads (capture/enrich/score), opportunities, inquiries/RFQs.
⟶ Quotations (sales), ⟶ Document AI (lead docs), ⟶ Planning (demand signals).

## Sales & OMS (domains/sales)
Quotations, customer PO intake (file + Document AI), sales orders, order lines, pricing/tax,
credit check, reservations ⟶inventory, allocation FEFO, partial shipments ⟶logistics,
invoices ⟶finance, customer service tasks, POS (retail counter mode).

## Supply Planning (domains/planning)
Forecast, demand plan, MRP run, safety stock, replenishment proposals, transfer-vs-buy-vs-produce,
expiry risk optimization. ⟶Procurement (purchase proposals), ⟶Production (planned orders),
⟶Inventory/Warehouse (transfers).

## Vendor Management (domains/vendors)
Vendor lifecycle (registration → approval → monitoring → requalification → suspension),
vendor documents with expiry tracking, qualifications, risk scoring, performance (OTD, QC
rejection rate), Vendor 360 view. Vendor Portal: RFQ/bids/contracts/PO ack/ASN/invoices/disputes.

## Strategic Sourcing (domains/sourcing)
RFI/RFP/RFQ, bids, reverse auctions (live decrement), technical/commercial evaluation,
BRA (`sourcing_award_recommendation`), award approval, contracts & BPAs with price slabs and
validity. ⟶Procurement POs, ⟶Vendors (qualified vendor lists).

## Procurement (domains/procurement)
PRs with budget/policy check and auto-approval, POs (contract release or sourcing), PO approval
matrix, acknowledgements, amendments (versioned), close-out. ⟶Logistics inbound, ⟶Warehouse
receiving, ⟶Finance matching/AP, ⟶Vendor performance.

## Inbound & Outbound Logistics (domains/logistics)
Inbound: transport booking, ETA, dock appointments, gate entry. Outbound: shipments,
consolidation, carriers/vehicles/routes, dispatch, tracking events, POD. ⟶Warehouse pick/pack,
⟶Sales order status, ⟶Returns pickup.

## Warehouse (domains/warehouse)
ASN receiving, gate/dock, GRN, quarantine, putaway, zones/locations, picking waves, packing,
staging, loading, cycle counts, physical inventory, replenishment tasks, expiry tasks,
transfer orders. ⟶Inventory ledger, ⟶QC sampling, ⟶Recall quarantine tasks.

## Inventory (domains/inventory)
Append-only `inventory_movements` ledger; `inventory_balances` projection; batch/lot master with
expiry; FEFO reservations; availability query (on-hand − reserved + in-transit); valuation
layers; block/unblock (quality/recall); traceability graph forward & reverse.
Consumed by: sales allocation, production issue, pharmacy dispense, returns, recall.

## QC / LIMS (domains/qc)
Specifications (approved master data), sampling plans, sample registration, test execution with
calculations, instruments, reagents/reference standards, OOS/OOT, CoA generation, stability.
⟶QA dispositions, ⟶Warehouse quarantine/release, ⟶Production in-process.

## QA / QMS (domains/qa)
Incoming inspection disposition, batch review/release, quality holds, deviations, CAPA, change
control, supplier quality (SCAR), complaints, internal audits, validation master, controlled
documents with training, risk register. ⟶Recall, ⟶Vendor scorecards, ⟶Production holds.

## Production / MES (domains/production)
Production plans, MRP planned orders, production orders (batch records eBMR/eBPR), BOM/formula
versions, material reservation & issue, dispensing/weighing, line clearance, equipment readiness
gates (calibration/maintenance/cleaning), process parameters, in-process controls/QC, yield
reconciliation, packaging, FG quarantine, deviation linkage. ⟶QC sampling, ⟶QA release,
⟶Inventory issue/receipt, ⟶Planning.

## Pharmacy (domains/pharmacy)
Prescription intake (upload + Document AI), drug matching, interactions/dupe-therapy flags,
compliance rules (Schedule H/H1/X, narcotics registers, quantity/refill limits, prescriber
validation), pharmacist queue, partial dispensing, dispense records, controlled-substance
registers. ⟶Sales/POS invoice, ⟶Inventory dispense, ⟶QC/QA for returns.

## Finance (domains/finance)
Supplier invoices (document AI), 2/3/4-way matching, AP aging & payment proposals, payments &
authorization, customer invoices/AR, credit/debit notes, cash application, bank reconciliation,
inventory valuation (weighted avg), COGS, write-offs (expiry/damage), accruals, GL entries &
export, budgets. Consumes events from every operational domain.

## Reverse Logistics & Recall (domains/reverse)
Return requests/RMA, pickup, return quarantine, inspection, disposition (restock/RTV/hold/
destruction/reject), customer credit ⟶finance; RTV ⟶vendor; recalls: global batch block,
locate everywhere, quarantine tasks, retrieval, disposal, reconciliation, closure.

## Pharmacovigilance (domains/safety)
Adverse event intake, safety cases, seriousness/expectedness, medical review, follow-ups, ICSR
export, signal tracking, PSUR/PADER schedules, link to quality complaints.

## Compliance & Audit (domains/compliance)
Licence registry (drug licence, GST, manufacturing licences) with expiry alerts, policy registry,
audit trail viewer, SoD checks, retention policies, regulatory submissions tracker.

## AI Operations (agents/)
Supervisor agent, domain agents (sales, customer service, supply chain, vendor/sourcing,
procurement, document AI, warehouse, QA/QC, plant, pharmacy, logistics, finance, compliance,
pharmacovigilance, analytics), Agent Tool Gateway (permission + policy + approval aware),
agent runs/tool-calls audit, self-resolution loop, human escalation categories.

## Platform
Auth/JWT/RBAC/SoD, workflow engine & timelines, policy engine, approval engine (Human Decision
Queue), event engine + outbox, audit engine, notifications (simulated email), document storage,
Document AI, idempotency, structured logging, health/metrics, seed data.
