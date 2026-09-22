# API Endpoint Index (Frozen)

Representative endpoints (full detail in OpenAPI at `/docs`):

## Auth & platform
POST /api/auth/login · POST /api/auth/refresh · GET /api/auth/me
GET /api/users · POST /api/users · PATCH /api/users/{id}
GET /health · GET /health/ready · GET /metrics
GET /api/analytics/command-center · GET /api/analytics/departments
GET /api/approvals?status=&category= · POST /api/approvals/{id}/decide
GET /api/workflows/{entity_type}/{entity_id}
GET /api/audit?entity_type=&entity_id= · GET /api/notifications
GET /api/documents · POST /api/documents/upload · POST /api/documents/{id}/process
GET /api/agents · POST /api/agents/supervisor/tick · GET /api/agents/tool-calls

## Masters
GET/POST /api/masters/warehouses · /products · /customers · /equipment · /specifications · /boms

## CRM & Sales
POST /api/crm/leads · POST /api/crm/leads/{id}/score · POST /api/crm/opportunities
POST /api/sales/quotations · POST /api/sales/quotations/{id}/send
POST /api/sales/customer-pos (multipart file → Document AI) · POST /api/sales/orders
POST /api/sales/orders/{id}/confirm · /allocate · /pick · /pack · /ship · /invoice
POST /api/sales/pos/sale (retail)

## Planning
POST /api/planning/mrp/run · GET /api/planning/supply/{product_id} · POST /api/planning/proposals/{id}/accept

## Vendors & Portal & Sourcing
POST /api/vendors · POST /api/vendors/{id}/documents · POST /api/vendors/{id}/approve
GET /api/vendors/{id}/performance · GET /api/vendors/{id}/360
POST /api/portal/vendor/rfqs/{id}/bids · POST /api/portal/vendor/pos/{id}/ack
POST /api/portal/vendor/asns · POST /api/portal/vendor/invoices
POST /api/sourcing/events (RFI/RFP/RFQ) · POST /api/sourcing/events/{id}/publish
POST /api/sourcing/events/{id}/bids · POST /api/sourcing/auctions/{id}/start · /bid · /close
POST /api/sourcing/awards (BRA) · /approve · POST /api/sourcing/contracts

## Procurement & Receiving
POST /api/procurement/prs · POST /api/procurement/prs/{id}/submit
POST /api/procurement/prs/{id}/convert → PO · POST /api/procurement/pos/{id}/send
POST /api/procurement/pos/{id}/ack · POST /api/logistics/inbound/asns · /arrive
POST /api/warehouse/grn (from ASN) · POST /api/warehouse/grn/{id}/putaway
POST /api/warehouse/pick · /pack · /dispatch · GET /api/warehouse/health

## Inventory / QC / QA / Production
GET /api/inventory/balances?product_id=&warehouse_id= · GET /api/inventory/batches
GET /api/inventory/traceability/{batch_id} · POST /api/inventory/reservations
POST /api/qc/samples · POST /api/qc/samples/{id}/results · POST /api/qc/oos
POST /api/qa/incoming/{grn_line}/disposition · POST /api/qa/deviations · /capas
POST /api/qa/batch-release/{production_order}/review · /release
POST /api/production/orders · /{id}/release · /issue-materials · /start · /complete-step
/complete · /pack · GET /{id}/batch-record

## Pharmacy
POST /api/pharmacy/prescriptions (file → docai) · GET /api/pharmacy/queue
POST /api/pharmacy/prescriptions/{id}/review (approve/reject/clarify)
POST /api/pharmacy/dispense

## Finance / Reverse / Safety / Compliance
POST /api/finance/supplier-invoices (file → docai) · POST /api/finance/invoices/{id}/match
POST /api/finance/credit-notes · POST /api/finance/payments/proposal · /authorize · /pay
POST /api/finance/reconciliation/run · GET /api/finance/gl
POST /api/reverse/returns · /{id}/rma · /receive · /inspect · /dispose
POST /api/reverse/recalls · /{id}/close
POST /api/safety/adverse-events · GET /api/safety/cases · POST /api/safety/cases/{id}/review
GET /api/compliance/licences · POST /api/compliance/licences (expiry monitor)
