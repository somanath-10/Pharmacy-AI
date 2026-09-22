# API & Module Map (Frozen)

Backend modular monolith — `backend/app`:

```
core/     config, database, redis_client, security, rbac, events(+outbox), workflow,
          approvals, policies, audit, idempotency, notifications, storage, ai_gateway,
          docai, logging, errors
domains/  masters, crm, sales, planning, vendors, sourcing, procurement, logistics,
          warehouse, inventory, qc, qa, production, pharmacy, finance, reverse,
          safety, compliance
agents/   gateway (tool gateway), supervisor, domain agent definitions
api/      main.py app assembly + routers (api/routes_*.py)
seed/     run.py demo data
```

## Router prefix map

| Prefix | Router | Domain |
|---|---|---|
| /api/auth | auth | login/refresh/me |
| /api/users | users | users & roles |
| /api/masters | masters | orgs/sites/warehouses/products/customers/equipment/specifications/boms |
| /api/crm | crm | markets/campaigns/leads/opportunities/inquiries |
| /api/sales | sales | quotations/customer-PO/sales orders/POS |
| /api/planning | planning | forecast/MRP/supply plans/proposals |
| /api/vendors | vendors | vendor lifecycle/documents/performance/360 |
| /api/portal/vendor | vendor portal | supplier self-service (RFQ/bids/PO/ASN/invoice) |
| /api/sourcing | sourcing | RFI/RFP/RFQ/bids/auctions/BRA/contracts |
| /api/procurement | procurement | PRs/POs/acks |
| /api/logistics | logistics | inbound/outbound/carriers/vehicles/POD |
| /api/warehouse | warehouse | ASN/gate/dock/GRN/putaway/pick/pack/cycle counts/transfers |
| /api/inventory | inventory | balances/movements/batches/reservations/traceability |
| /api/qc | qc | specs/samples/tests/OOS/CoA |
| /api/qa | qa | dispositions/deviations/CAPA/holds/release |
| /api/production | production | plans/production orders/batch records/issues/equipment gates |
| /api/pharmacy | pharmacy | prescriptions/pharmacist queue/dispense |
| /api/finance | finance | invoices/matching/payments/AR/reconciliation/GL |
| /api/reverse | reverse | returns/RMA/dispositions/recalls |
| /api/safety | safety | adverse events/safety cases/signals |
| /api/compliance | compliance | licences/policies/SoD |
| /api/documents | documents | upload/list/process (Document AI) |
| /api/approvals | approvals | Human Decision Queue (list/decide) |
| /api/workflows | workflows | entity workflow timelines |
| /api/audit | audit | audit trail queries |
| /api/notifications | notifications | list (simulated email) |
| /api/agents | agents | status/runs/tool-calls; POST /agents/supervisor/tick |
| /api/analytics | analytics | KPIs for Command Center & departments |
| /health | health | liveness/readiness/deps |

OpenAPI: `http://localhost:8000/docs`. Endpoint index: `14_API_ENDPOINT_INDEX.md`.
