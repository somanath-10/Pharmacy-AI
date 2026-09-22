# Pharma / Pharmacy AI Operating System

An AI-native pharmaceutical & pharmacy enterprise operating system covering the complete business lifecycle:

**Market → Lead → Inquiry → Quotation → Customer PO → Sales Order → Supply Planning → Sourcing → Procurement → ASN → Inbound → Receiving → QC/QA → Inventory → Production → FG Release → Allocation → (Pharmacy Dispensing) → Pick/Pack → Logistics → Delivery → Invoice → Payment → Reconciliation → Post-Market Safety**

…with Finance, Quality, Compliance, Audit, Documents, Traceability, Returns, Recall, Pharmacovigilance and **AI agents** running horizontally across everything.

## Core principle

```
NORMAL WORK  ──▶  AI Agents + Rules + Workflow Engine  ──▶  Automatically executed
IMPORTANT / REGULATED / PHYSICAL / EXCEPTION  ──▶  Human Decision Queue
```

Humans approve, review and reject. AI does the rest. Agents never write to MongoDB directly — every agent action passes through the **Agent Tool Gateway → Policy Engine → Approval Engine → Domain API → validated transaction → domain event → audit log** chain.

## Stack

| Layer | Technology |
|---|---|
| Frontend | React 18 + Vite + React Router |
| Backend | Python 3.12 + FastAPI (modular monolith) |
| Database | MongoDB (strict schemas, indexes, versioning, idempotency, append-only ledgers) |
| Cache/Locks | Redis (optional at dev time — in-process fallback) |
| Documents | S3-compatible object storage / MinIO (GridFS/local fallback) |
| AI | OpenAI API (deterministic offline fallback when key absent) |
| Auth | JWT / OAuth2 + RBAC + policy-based authorization + segregation of duties |
| Deploy | Docker + Docker Compose |
| Observability | Structured JSON logs, request IDs, health endpoints |

## Repository layout

```
pharmacy_ai_os/
├── frontend/          React app (10 workspaces + AI Command Center + Human Decision Queue)
├── backend/           FastAPI modular monolith
│   └── app/
│       ├── core/      config, db, security, events+outbox, workflow engine, policy/approval
│       │              engines, audit, notifications, storage, AI gateway, Document AI
│       ├── domains/   masters, crm, sales, planning, vendors, sourcing, procurement,
│       │              logistics, warehouse, inventory, qc, qa, production, pharmacy,
│       │              finance, reverse, safety, compliance
│       ├── agents/    tool gateway, supervisor, domain agents
│       └── seed/      demo/master data seeding
├── docs/              00–15 frozen architecture documents
├── infrastructure/    docker assets, mongo init, nginx
├── scripts/           dev / seed / test scripts
├── docker-compose.yml
└── Makefile
```

## Quickstart (Docker)

```bash
cp .env.example .env
docker compose up --build
# Backend  http://localhost:8000/docs
# Frontend http://localhost:5173
```

## Quickstart (local dev)

```bash
# 1. MongoDB on :27017 (and optionally Redis on :6379)
# 2. Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env   # adjust as needed
uvicorn app.main:app --reload --port 8000

# 3. Seed demo data (idempotent)
python -m app.seed.run

# 4. Frontend
cd ../frontend
npm install
npm run dev
```

### Demo logins (seeded)

| Role | Email | Password |
|---|---|---|
| Super Admin | admin@pharmaos.local | Admin@123 |
| Procurement | buyer@pharmaos.local | Buyer@123 |
| QA Manager | qa@pharmaos.local | Qa@123 |
| QC Analyst | qc@pharmaos.local | Qc@123 |
| Pharmacist | pharmacist@pharmaos.local | Pharm@123 |
| Finance | finance@pharmaos.local | Fin@123 |
| Warehouse | warehouse@pharmaos.local | Wh@123 |
| Sales | sales@pharmaos.local | Sales@123 |
| Vendor Portal | vendor@acmecorp.com | Vendor@123 |

## Run tests

```bash
cd backend && pytest -q            # end-to-end workflow tests (P2P, O2C, production, recall, idempotency…)
cd frontend && npm run build
```

## Documentation

Start with `docs/00_PROJECT_BLUEPRINT.md`, then `docs/01_MASTER_ARCHITECTURE.md`. The full document index is in `docs/README.md`.
