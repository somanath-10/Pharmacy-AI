# End-to-End Workflows (Frozen)

Every workflow below is implemented in code and exercised by tests (`backend/tests/`).

## WF-01 Market-to-Order
Market research → lead capture → enrichment → scoring → opportunity → inquiry/RFQ →
requirement understanding → quotation → commercial approval (policy) → customer PO (PDF/document
AI) → sales order. Retail-only deployments disable this module (feature flag `enable_market_to_order`).
*Code:* `domains/crm`, `domains/sales`, `core/docai.py`.

## WF-02 Supply Planning
Demand sources: historical sales, confirmed orders, forecast, seasonality, promotions.
Supply inputs: on-hand, reserved, in-transit (open POs/ASNs), open production orders, safety stock,
supplier lead time, expiry risk. Decision: stock enough? → transfer from excess warehouse? →
produce? → procure. Emits `planning.replenishment_needed`, `planning.transfer_proposed`,
`planning.production_proposed`, `planning.purchase_proposed`.
*Code:* `domains/planning/service.py` (`compute_supply_plan`).

## WF-03 Vendor Lifecycle
Request → registration → documents (licence, bank, manufacturer authorization) → KYC/compliance
checks → commercial qualification → QA qualification → risk analysis → approval → ACTIVE →
continuous performance monitoring (OTD, rejection rate) → requalification → suspend/block.
Emits `vendor.approved`, `vendor.suspended`, `vendor.licence_expiry_warning`.
*Code:* `domains/vendors`.

## WF-04 Vendor Portal
Supplier identity (separate user role `SUPPLIER`) can: view RFQs, submit bids, join reverse
auctions, accept contracts, acknowledge POs, create ASNs, upload shipping docs, submit invoices,
view quality issues / payment status / disputes. All portal actions are audited with actor type VENDOR.
*Code:* `domains/vendors/portal.py`, `api/routes_vendor.py`.

## WF-05 Strategic Sourcing
Requirement → existing contract/BPA? yes → release; no → RFI/RFP/RFQ → qualified vendors → bids →
technical + commercial evaluation → reverse auction (optional) → final analysis → award
recommendation (**BRA**, stored generically as `sourcing_award_recommendation`) → award approval →
contract/BPA → PO. Emits `rfq.published`, `bid.received`, `auction.completed`, `award.approved`.
*Code:* `domains/sourcing`.

## WF-06 Purchase-to-Pay
Requirement → PR → budget/policy check → auto-approval or human → contract/BPA/sourcing → PO →
approval → vendor ack → ASN → inbound logistics → gate/dock → receiving → GRN → quarantine →
QC/QA → accept/hold/reject → putaway → inventory → supplier invoice (document AI) → 2/3/4-way
match → AP → payment proposal → authorization → payment → bank reconciliation → vendor performance.
*Code:* `domains/procurement`, `domains/warehouse/receiving.py`, `domains/qc`, `domains/finance`.

## WF-07 Four-Way Match
PO + GRN + QA-accepted qty + invoice. Differences (e.g., QA rejected 30 of 1000) route to the
Finance Agent which checks the QA record, requests a credit note/corrected invoice (simulated
communication task), re-runs the match; only unresolved disputes reach Finance staff.
*Code:* `domains/finance/matching.py`.

## WF-08 Warehouse Inbound
ASN → gate → dock → unload → scan → batch/expiry capture → GRN → quarantine → QC sample →
putaway (FEFO-aware bins). *Code:* `domains/warehouse/receiving.py`.

## WF-09 Warehouse Internal & Outbound
Zones/aisles/racks/bins; batch inventory; FEFO allocation; reservations; transfers; replenishment;
cycle counting; expiry management; quarantine. Outbound: order → allocation → wave → pick → scan →
pack → stage → load → dispatch. *Code:* `domains/warehouse`, `domains/inventory`.

## WF-10 Reverse Logistics
Return request → policy validation → RMA → pickup → receipt → RETURN_QUARANTINE → inspection →
disposition: restock (where permitted) / RTV / quality hold / recall hold / destruction / reject.
Returns never re-enter available stock without QA disposition. *Code:* `domains/reverse`.

## WF-11 Recall
Recall notice → product/batch → global block (stop reservation/picking/dispensing) → locate
warehouses, in-transit shipments, production batches, customers → quarantine tasks → return or
disposal → reconciliation → closure. *Code:* `domains/reverse/recall.py`.

## WF-12 QC / LIMS
Sample registration → sampling plan → specification-driven testing (raw/packaging/in-process/FG/
stability) → results & calculations → OOS/OOT handling → CoA generation. Specifications come from
approved master data only. *Code:* `domains/qc`.

## WF-13 QA / QMS
Incoming release, batch review, batch release, quality holds, deviations, CAPA, change control,
OOS investigations, risk assessment, supplier quality, complaints, audits, validation, controlled
documents, training. QC tests; QA reviews/assures/releases. *Code:* `domains/qa`.

## WF-14 Plant / Production (MES)
Demand → production plan → MRP → production order → approved BOM/master formula → material
reservation → dispensing/weighing → warehouse issue → line clearance → equipment readiness
(calibration/maintenance/cleaning gates) → batch start → manufacturing → in-process controls →
in-process QC → bulk → packaging → yield reconciliation → finished batch → FG quarantine →
final QC → QA review → QA release → FG warehouse. eBMR/eBPR records every step immutably.
*Code:* `domains/production`.

## WF-15 Order-to-Cash
Customer order → OMS → customer/credit validation → prescription check (where applicable) →
availability → pricing → reservation (QA-released stock, FEFO) → WMS allocation → pick → pack →
dispense (pharmacy) → invoice → payment/credit → shipment → delivery → POD → AR → cash
application → reconciliation → close. *Code:* `domains/sales`, `domains/pharmacy`, `domains/finance`.

## WF-16 Pharmacy Dispensing
Order → prescription required? → upload → document AI extraction (drug/strength/dose) → drug
master matching → compliance rules (Schedule H/H1/X, narcotics, quantity limits, refills, expiry) →
registered pharmacist review → approve/reject/clarify → reservation → pick → final check →
dispense → invoice. *Code:* `domains/pharmacy`.

## WF-17 Logistics
Inbound: PO ack → transport → ETA → appointment → gate/dock. Outbound: ready-to-ship → shipment
planning → consolidation → carrier/rider → vehicle → route optimization → loading → dispatch →
tracking → delivery → POD. *Code:* `domains/logistics`.

## WF-18 Finance
Event-driven postings from all domains: GRN→inventory accounting; supplier invoice→AP; material
issue→WIP; production complete→FG; customer invoice→AR/revenue; sale/dispense→COGS; return→credit
note; expiry/damage→write-off; payment→cash/bank. Modules: AP, AR, matching, credit/debit notes,
payments, cash application, reconciliation, inventory valuation, COGS, accruals, GL export.
*Code:* `domains/finance`.

## WF-19 Pharmacovigilance
Adverse event intake → safety case → patient/reporter → suspect medicine → batch → duplicate check
→ seriousness → expectedness → medical review → ICSR/follow-up → signal tracking → PSUR.
Product-quality complaints stay in QMS; one case can link both. *Code:* `domains/safety`.

## WF-20 Compliance & Audit
Licence registry with expiry monitoring, policy enforcement points, append-only audit, SoD
controls (vendor create/bank change/PO/invoice approval/payment authorization split), regulated
record retention. *Code:* `domains/compliance`, `core/audit.py`, `core/rbac.py`.

## WF-21 Document Intelligence (shared)
Document → classification → OCR/vision → structured extraction (OpenAI) → master-data matching →
deterministic validation → confidence → workflow routing. Handles customer POs, RFQ responses,
quotations, licences, certificates, CoAs, invoices, prescriptions, packing lists, ASN docs,
contracts, QC reports, batch records, PODs. *Code:* `core/docai.py`.
