# Pharma AI OS — Autonomous Enterprise Core

One AI-native pharmaceutical / pharmacy operating system covering **Market-to-Order, Supply
Planning, Source-to-Contract, Purchase-to-Pay, Warehouse & Inventory, QA/QC, Manufacturing,
Pharmacy Dispensing, Order-to-Cash, Logistics, Finance, Reverse Logistics, Post-Market Safety,
Compliance and Audit** — in a single application, with AI agents removing routine human work.

```
NORMAL WORK                    IMPORTANT / REGULATED / PHYSICAL / EXCEPTION
     ↓                                      ↓
AI Agents + Rules + Workflow          Human Decision Queue
     ↓                                      ↓
Automatically Execute                 Human approves / reviews / rejects
```

## Stack

| Layer      | Technology |
|------------|------------|
| Frontend   | React 19 + Vite + React Router (reference blue palette design system) |
| Backend    | Python 3.13 + FastAPI, modular monolith (`backend/app/domains/*`) |
| Database   | MongoDB (transactions, unique indexes, append-only ledgers, optimistic versioning) |
| AI         | OpenAI gateway with deterministic offline fallbacks (`app/core/ai_gateway.py`) |
| Platform   | Workflow/state-machine engine, policy engine, approval engine, event outbox, audit, notifications (simulated email) |
| Deployment | Docker + docker-compose (mongo, redis, api, nginx-served web) |

## Run it

### Local (no Docker)

```bash
# 1. Backend — master seed only
cd backend
../.venv/bin/python -m uvicorn app.main:app --port 8000   # venv at repo root

# 2. Backend — with rich demo dataset (recommended for the UI)
SEED_DEMO=1 ../.venv/bin/python -m uvicorn app.main:app --port 8000

# 3. Frontend
cd frontend
npm install && npm run dev          # http://localhost:5173 (proxies /api → :8000)
```

### Docker

```bash
make up        # builds and starts mongo + redis + api + web
# UI:  http://localhost:8080     API docs: http://localhost:8000/docs
```

### Demo accounts (seeded)

| Login | Password | Role |
|---|---|---|
| admin@pharmaos.local | Admin@123 | Super Admin |
| buyer@pharmaos.local | Buyer@123 | Procurement / Buyer |
| qa@pharmaos.local | Qa@123 | QA authority |
| qc@pharmaos.local | Qc@123 | QC analyst |
| pharmacist@pharmaos.local | Pharm@123 | Pharmacist |
| finance@pharmaos.local | Fin@123 | Finance |
| warehouse@pharmaos.local | Wh@123 | Warehouse |
| sales@pharmaos.local | Sales@123 | Sales |
| vendor@acmecorp.com | Vendor@123 | Supplier portal |

## Workspaces (UI)

1. **AI Command Center** — KPIs, live inputs, AI orchestration core, outputs & actions, department health, decision queue
2. **Customers & Sales** — CRM, quotations, customer PO intake (Document AI), sales orders, O2C
3. **Supply Chain** — MRP proposals, transfer-vs-buy, planning
4. **Vendors & Procurement** — vendor lifecycle, sourcing events, RFQ/auction, PR → PO, receipts
5. **Warehouse & Inventory** — ASN → GRN → QC quarantine → putaway, ledger, batches, FEFO
6. **Quality** — QC/LIMS samples, OOS, QA dispositions, deviations/CAPA, batch release
7. **Plant / Production** — production orders, material reservation, eBMR steps, equipment gates
8. **Pharmacy** — prescription upload (AI extraction), pharmacist review, dispensing, POS
9. **Logistics** — shipments, dispatch, tracking, POD
10. **Finance & Governance** — supplier invoices, 4-way match, payments, reconciliation, credit notes, audit

Plus **Human Decision Queue** (approve/review/reject with assembled evidence) and **Workflow
Viewer** (per-entity lifecycle timeline with agent/user, reasons, timestamps).

## Backend layout

```
backend/app/
├── api/          # FastAPI routers per module
├── core/         # config, database, security/rbac, workflow engine, policies,
│                 # approvals (+ human callbacks), events/outbox, audit, idempotency,
│                 # docai, ai_gateway, notifications, storage
├── domains/      # sales, crm, planning, vendors, sourcing, procurement, inventory,
│                 # warehouse, qc, qa, production, pharmacy, logistics, finance,
│                 # reverse, safety, compliance, masters, analytics
├── agents/       # agent gateway: permission check → policy → domain API → audit
└── seed/         # idempotent master seed + rich demo lifecycle dataset
```

## Tests

```bash
cd backend && ../.venv/bin/python -m pytest tests/ -q     # 16 workflow/E2E tests
```

Covers: full P2P (PR→PO→ASN→GRN→QC→QA→putaway→invoice→4-way match→payment), partial QA
rejection + credit note re-match, OOS investigation, production with material reservation +
equipment gates + eBMR, O2C with shipment delivery interlink, prescription review + dispensing,
returns/RTV/disposal, recall with global block, reconciliation, idempotency (GRN retry), RBAC
denials and segregation of duties.

## Interlinks (cross-domain wiring)

- Sales order demand → MRP proposals → PR/PO/production suggestions
- PR convert → auto vendor/contract resolution from active BPAs → PO
- PO approval limits → Human Decision Queue with evidence
- ASN → GRN → QC sample auto-registration → QA disposition → inventory block/unblock → putaway
- Shipment delivery → sales order DELIVERED → customer invoice → AR → reconciliation
- Payment execution → PO workflow timeline "payment" node DONE
- Batch release → inventory unblock → allocation eligibility
- Recall → global batch block → stop reservations/picks/dispense
- Adverse event ↔ product/batch linkage; quality complaints ↔ QA/QMS

## Operations

- `GET /health`, `GET /health/ready`
- Structured JSON logs with request IDs
- Idempotency-Key middleware honoured by intake endpoints
- Append-only inventory ledger (`inventory_movements`) as source of truth
- Audit events on every state change with actor/policy/reason
- Configuration via env vars — see `.env.example` and `docs/12_PRODUCTION_RUNBOOK.md`

## Docs

Full frozen architecture lives in `docs/00…15_*.md` (blueprint, master architecture, workflows,
feature catalog, agent/human matrix, data model, events & states, API map, security, UI/UX,
test plan, coverage matrix, runbook, agent tool contracts, endpoint index, validation report).

## ZIP deliverable

```bash
make zip        # or: bash scripts/make_zip.sh   → pharmacy_ai_os.zip
```
