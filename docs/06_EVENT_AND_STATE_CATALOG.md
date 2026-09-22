# Event & State Machine Catalog (Frozen)

## Events (published via outbox; handlers idempotent)

sales.lead_captured · sales.opportunity_created · sales.inquiry_received · sales.quotation_sent ·
sales.order_confirmed · sales.order_cancelled · planning.material_shortage ·
planning.replenishment_needed · planning.transfer_proposed · planning.production_proposed ·
planning.purchase_proposed · vendor.approved · vendor.suspended · vendor.licence_expiry_warning ·
rfq.published · bid.received · auction.completed · award.approved · contract.created ·
pr.approved · po.created · po.approved · po.acknowledged · asn.created · shipment.arrived ·
grn.created · qc.sampled · qc.passed · qc.failed · qa.released · qa.rejected · qa.hold_applied ·
deviation.created · capa.created · inventory.available · inventory.blocked ·
production.started · production.completed · batch.released · batch.blocked ·
prescription.approved · prescription.rejected · dispense.completed ·
shipment.dispatched · delivery.completed · invoice.received · invoice.match_failed ·
invoice.matched · payment.completed · return.received · return.dispositioned ·
batch.recalled · recall.closed · adverse_event.received · document.processed ·
exception.created · exception.resolved · approval.requested · approval.decided

## State machines

### Purchase Order
DRAFT → PENDING_APPROVAL → APPROVED → SENT → ACKNOWLEDGED → PARTIALLY_RECEIVED → RECEIVED →
CLOSED; any of {APPROVED…RECEIVED} → CANCELLED (amendments create new version, audited).

### Purchase Requisition
DRAFT → PENDING_APPROVAL → APPROVED → CONVERTED / REJECTED.

### Raw material batch
EXPECTED → RECEIVED → QUARANTINE → SAMPLING → QC_TESTING → QA_REVIEW → RELEASED;
alt: → QUALITY_HOLD → REJECTED → (RTV | DISPOSAL).

### RFQ / Sourcing event
DRAFT → PUBLISHED → BIDDING → EVALUATION → RECOMMENDATION → AWARDED → CONTRACTED; → CANCELLED.
Auction: SCHEDULED → LIVE → COMPLETED / CANCELLED.

### Sales order
DRAFT → CONFIRMED → ALLOCATED → PICKING → PACKED → DISPATCHED → DELIVERED → INVOICED → CLOSED;
alt: → CANCELLED; pharmacy branch adds PENDING_RX → RX_APPROVED before ALLOCATED.

### Prescription
UPLOADED → EXTRACTED → VALIDATED → PHARMACIST_REVIEW → APPROVED | REJECTED | CLARIFICATION
→ DISPENSED.

### Production order
PLANNED → RELEASED → DISPENSING → IN_PROCESS → PACKAGING → COMPLETED → FG_QUARANTINE →
QC_COMPLETE → QA_REVIEW → RELEASED; alt: → ON_HOLD → (RESUMED | CANCELLED).

### QC sample
REGISTERED → SAMPLING → TESTING → RESULTS_ENTERED → REVIEW → COMPLETED; OOS branch →
OOS_INVESTIGATION → COMPLETED.

### GRN
DRAFT → POSTED → QC_PENDING → QC_PASSED | QC_PARTIAL | QC_FAILED → PUTAWAY_DONE → CLOSED.

### Supplier invoice
RECEIVED → EXTRACTED → IN_MATCHING → MATCHED | MATCH_FAILED → APPROVED → SCHEDULED → PAID;
alt: DISPUTED → CREDIT_NOTE_RECEIVED → (MATCHED | WRITTEN_OFF).

### Customer invoice
DRAFT → ISSUED → PARTIALLY_PAID → PAID; alt: CREDIT_NOTE_ISSUED → WRITTEN_OFF.

### Shipment (outbound)
PLANNED → LOADING → DISPATCHED → IN_TRANSIT → DELIVERED (POD) → CLOSED; alt: EXCEPTION.

### Return / RMA
REQUESTED → APPROVED(RMA) → PICKUP_SCHEDULED → PICKED → RECEIVED → RETURN_QUARANTINE →
INSPECTED → DISPOSITIONED → (RESTOCKED | RTV | DESTROYED | REJECTED | QUALITY_HOLD).

### Recall
NOTIFIED → IN_PROGRESS → CONTAINED → RECONCILED → CLOSED; alt: → CANCELLED (false alarm).

### Deviation / CAPA
Deviation: OPEN → INVESTIGATION → APPROVED → CLOSED. CAPA: IDENTIFIED → PLANNED →
IMPLEMENTATION → VERIFICATION → CLOSED.

### Safety case
NEW → TRIAGE → DUPLICATE_CHECK → MEDICAL_REVIEW → FOLLOW_UP → REPORTABLE → CLOSED.

### Approval / Human Decision Queue item
PENDING → DECIDED (APPROVED | REJECTED | HELD | CLARIFIED) | EXPIRED.

### Workflow transitions — enforced by `core/workflow.py`
`transition(entity, from, to)` validates against registered machines, records timeline entry
(actor, previous, next, reason, timestamps), emits `<entity>.<transition>` event, writes audit.
