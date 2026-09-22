# Implementation & Test Plan (Frozen)

## Build order (as implemented in this repository)

1. Platform foundation: config, Mongo, Redis(optional), security/RBAC/SoD, audit, idempotency,
   workflow engine, event engine + outbox, policy engine, approval engine, notifications,
   storage, AI gateway, Document AI.
2. Masters + immutable inventory ledger.
3. P2P: vendors → sourcing → PR → PO → ASN → inbound → GRN → QC/QA → inventory → invoice →
   matching → AP → payment → reconciliation.
4. Production/MES + QC/LIMS + QA/QMS incl. eBMR/eBPR and batch release.
5. O2C: CRM → quotation → customer PO (doc AI) → sales order → Rx (where needed) → reservation →
   WMS pick/pack → logistics → invoice → AR → reconciliation.
6. Reverse logistics, recall, compliance, documents, pharmacovigilance.
7. Agent tools + Supervisor + Human Decision Queue + AI Command Center.
8. Frontend workspaces; Docker assets; seed; runbook; tests; ZIP.

## Test scenarios (backend/tests)

| Test | Scenario |
|---|---|
| test_health | liveness |
| test_p2p_full_flow | vendor→RFQ→bid→award→contract→PR→PO→ack→ASN→GRN→QC pass→putaway→invoice→4-way match→payment |
| test_p2p_qa_rejection_4way | QA rejects 30/1000 → mismatch → credit-note path resolves match |
| test_po_approval_matrix | small PO auto-approved; high-value goes to Human Decision Queue; SoD blocks self-approval |
| test_production_batch_release | BOM issue → eBMR steps → equipment gate blocks on invalid calibration → fix → complete → QC → QA release → FG receipt |
| test_o2c_with_prescription | lead→quote→PO→SO→Rx upload→pharmacist approve→allocate FEFO→pick→pack→ship→deliver→invoice→payment→reconcile |
| test_recall_flow | recall notice → global block → tasks → closure |
| test_returns_rtv | return → quarantine → inspection → RTV |
| test_idempotency | duplicate GRN/invoice/payment with same key → single effect |
| test_workflow_invalid_transition | illegal state change rejected & audited |
| test_vendor_lifecycle_and_sod | licence expiry warning; SoD violation blocked |
| test_docai_invoice_extraction | offline fallback extraction works; confidence recorded |
| test_agent_gateway | forbidden tool absent; escalation created over policy limit |

Run: `cd backend && pytest -q` (uses mongod at MONGODB_URI; test DB `pharmacy_ai_os_test`).
