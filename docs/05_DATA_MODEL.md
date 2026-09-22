# Data Model (Frozen)

MongoDB. Conventions: `created_at`, `updated_at`, `version` (optimistic concurrency) on critical
collections; human ids like `PO-2026-00889`; `status` driven by state machines; soft references by
string ids (`product_id`, `batch_id`…). Regulated collections are append-only or version-superseded.

## Core collections (implemented in code; indexes created at startup)

**Masters** — organizations, sites, warehouses, locations, users, roles(embedded), products,
customers, equipment, specifications, boms, price_lists, departments, budgets.

**Inventory** — `inventory_movements` (append-only; movement_id, product_id, batch_id,
warehouse_id, location_id, movement_type, quantity signed, uom, reference_type/id, performed_by,
created_at), `inventory_balances` (projection), `batches` (batch_id, product_id, mfg/expiry, qa_status,
storage_conditions, block flags), `reservations` (sales_order/dispense links, FEFO picks).

**Vendors** — vendors, vendor_documents (type, number, expiry, file), vendor_qualifications,
vendor_performance_events. **Sourcing** — sourcing_events (RFI/RFP/RFQ), bids, auctions,
auction_bids, sourcing_award_recommendation (BRA), contracts (price slabs, validity).

**Procurement** — purchase_requisitions, purchase_orders (lines, contract_id, approvals),
po_acknowledgements, asns (lines ↔ PO lines).

**Warehouse** — receipts/GRNs (lines with batch/expiry), grn_qc_links, putaway_tasks, pick_tasks,
waves, pack_tasks, cycle_counts, transfer_orders, gate_entries, dock_appointments.

**QC/QA** — qc_samples, qc_tests, qc_results, oos_records, certificates_of_analysis,
specifications; deviations, capas, change_controls, quality_holds, batch_releases, complaints,
audits(quality), training_records, controlled_documents.

**Production** — production_plans, production_orders, batch_records (eBMR/eBPR append steps),
material_issues, equipment_logs, line_clearances, yield_reconciliations.

**CRM/Sales** — markets, campaigns, leads, opportunities, inquiries, quotations, customer_pos,
sales_orders, pos_transactions.

**Pharmacy** — prescriptions (extracted fields, match, review), dispenses, controlled_registers.

**Logistics** — inbound_shipments (ASN transport), shipments, carriers, vehicles, routes,
delivery_attempts, pods.

**Finance** — supplier_invoices, customer_invoices, invoice_matches (2/3/4-way result),
credit_notes, debit_notes, payments, cash_applications, reconciliations, gl_entries (append-only),
inventory_valuations, write_offs, budgets.

**Reverse** — return_requests, rmAs, return_orders, return_inspections, dispositions, destructions,
recalls, recall_tasks, recall_links.

**Safety** — safety_cases, adverse_events, follow_ups, signals, psur_reports.

**Compliance** — licences (org + vendor), policy_rules, sod_rules, regulatory_submissions.

**Platform** — workflows (entity timeline), approvals (Human Decision Queue), exceptions,
documents (metadata + file ref), notifications, audit_events (append-only),
agent_runs, agent_decisions, agent_tool_calls, outbox_events, idempotency_keys, counters.

## Integrity rules

- Indexes: unique on human ids (po_id, grn_id, invoice_id, movement_id, …), compound on
  (product_id, warehouse_id, status), (batch_id), (status, due_at) for queues; TTL none (retention).
- Transactions (replica set) wrap multi-document invariants (ledger + balance + reservation).
- `outbox_events` backs at-least-once delivery; handlers are idempotent.
- Documents: metadata in `documents`, bytes in GridFS/local/S3; virus-scan hook point.
