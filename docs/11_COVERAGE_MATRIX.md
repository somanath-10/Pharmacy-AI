# Coverage Matrix — Discussion → Implementation (Frozen)

| # | Discussed topic | Where implemented | Tests |
|---|---|---|---|
| 1 | Market-to-Order (lead→PO intake) | domains/crm, domains/sales, core/docai | test_o2c_with_prescription |
| 2 | Supply planning (transfer/produce/buy) | domains/planning | test_p2p_full_flow (proposal path) |
| 3 | Vendor lifecycle + Vendor 360 | domains/vendors | test_vendor_lifecycle_and_sod |
| 4 | Vendor Portal | domains/vendors/portal.py, routes_vendor | portal endpoints (auth role SUPPLIER) |
| 5 | Strategic sourcing, RFQ/auction/BRA | domains/sourcing | test_p2p_full_flow |
| 6 | P2P end-to-end | procurement+warehouse+qc+qa+finance | test_p2p_full_flow |
| 7 | 4-way match + credit note loop | finance/matching.py | test_p2p_qa_rejection_4way |
| 8 | WMS inbound/internal/outbound/reverse | domains/warehouse | p2p/o2c/recall tests |
| 9 | Ledger-driven inventory + FEFO | domains/inventory | test_p2p_full_flow, test_o2c |
| 10 | Batch/lot traceability (fwd+rev) | inventory/traceability.py | traceability endpoint assertions |
| 11 | QC/LIMS + OOS + CoA | domains/qc | test_production_batch_release |
| 12 | QA/QMS (deviation/CAPA/release) | domains/qa | test_production_batch_release |
| 13 | Plant/MES + eBMR/eBPR + equipment gates | domains/production | test_production_batch_release |
| 14 | O2C + pharmacy branch | sales + pharmacy + finance | test_o2c_with_prescription |
| 15 | Prescription OCR + pharmacist authority | pharmacy + docai | test_o2c_with_prescription |
| 16 | Logistics inbound/outbound/POD | domains/logistics | test_o2c_with_prescription |
| 17 | Reverse logistics (RMA/RTV/destruction) | domains/reverse | test_returns_rtv |
| 18 | Recall forward+reverse + global block | reverse/recall.py | test_recall_flow |
| 19 | Finance events + GL + valuation | finance/service.py | ledger assertions in p2p |
| 20 | Pharmacovigilance (AE/ICSR/PSUR) | domains/safety | case lifecycle assertions |
| 21 | Shared Document AI pipeline | core/docai.py | test_docai_invoice_extraction |
| 22 | Agent architecture + Supervisor | agents/* | test_agent_gateway |
| 23 | Agent tool gateway safety | agents/gateway.py | test_agent_gateway |
| 24 | Human-minimization + escalation taxonomy | agents/supervisor.py, approvals | test_agent_gateway |
| 25 | Human Decision Queue | core/approvals.py + UI | test_po_approval_matrix |
| 26 | AI Command Center KPIs | analytics service + UI | KPI endpoint assertions |
| 27 | Workflow Viewer timelines | core/workflow.py + UI | workflow endpoint assertions |
| 28 | Mongo domain model + indexes | core/database.py ensure_indexes | startup test |
| 29 | State machines | core/workflow.py registry | test_workflow_invalid_transition |
| 30 | Event-driven interlinks + outbox | core/events.py | event assertions across tests |
| 31 | Notifications (email simulated) | core/notifications.py | assertion in o2c test |
| 32 | RBAC + SoD + policy conditions | core/security.py, rbac.py, policies.py | test_vendor_lifecycle_and_sod |
| 33 | Audit append-only | core/audit.py | audit assertions across tests |
| 34 | Idempotency keys | core/idempotency.py | test_idempotency |
| 35 | Email static V1 | notifications | — |
| 36 | Docs 00–15 + repo shape | docs/, repo tree | — |
| 37 | Testing strategy incl. restart/dup | backend/tests | test_idempotency, workflow tests |
| 38 | Docker deployment assets | Dockerfile*, docker-compose.yml, infra/ | compose config |
| 39 | Seed/demo data + logins | backend/app/seed/run.py | seed idempotency check |
| 40 | Production runbook | docs/12_PRODUCTION_RUNBOOK.md | — |
