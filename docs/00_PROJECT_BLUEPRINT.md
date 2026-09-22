# Project Blueprint — Pharma / Pharmacy AI Operating System

One AI-native enterprise operating system covering **Market-to-Order, Supply Planning,
Source-to-Contract, Purchase-to-Pay, Warehouse & Inventory, QA/QC, Manufacturing, Pharmacy
Dispensing, Order-to-Cash, Logistics, Finance, Reverse Logistics, Post-Market Safety, Compliance
and Audit** — in a single application, with AI agents removing routine human work.

## The operating principle

```
NORMAL WORK            ──▶  AI Agents + Rules + Workflow Engine  ──▶  Automatically executed
IMPORTANT / REGULATED / PHYSICAL / EXCEPTION  ──▶  Human Decision Queue
```

Humans do not perform work merely because traditional ERP required screen-clicking. Human work is
reserved for: **physical activities, pharmacist authority, QA batch-release authority, major
financial approvals, strategic decisions, security/fraud cases and unresolved exceptions.**

## Scope freeze

The complete enterprise chain implemented in this repository:

```
MARKET → LEAD → OPPORTUNITY → INQUIRY/RFQ → QUOTATION → CUSTOMER PO → SALES ORDER
→ DEMAND PLAN → SUPPLY PLAN → BUY / TRANSFER / PRODUCE
→ SOURCE → VENDOR → CONTRACT/BPA → PURCHASE → ASN → INBOUND LOGISTICS
→ RECEIVING/GRN → QUARANTINE → QC/QA → RAW MATERIAL INVENTORY
→ PLANT/MANUFACTURING → IN-PROCESS QC → FINISHED PRODUCT → FINAL QC → QA RELEASE
→ FG WAREHOUSE → ORDER ALLOCATION → (PHARMACY DISPENSE) → PICK/PACK
→ OUTBOUND LOGISTICS → DELIVERY → INVOICE → PAYMENT → RECONCILIATION → POST-MARKET
```

Running horizontally across the entire lifecycle: **Finance, Quality, Compliance, Audit,
Documents, Traceability, Returns, Recall, Pharmacovigilance, AI Agents.**

## Non-negotiable architecture rules

1. **Agents never write to MongoDB directly.** Agent → Tool Gateway → Policy Engine → Approval
   Engine → Domain API → validated transaction → domain event → audit log.
2. **LLMs are never the source of truth.** Quantities, stock states, tax, pricing limits,
   accounting values, approved specifications, permissions, approval limits, compliance
   enforcement, QA release status, prescription restrictions and document versions are decided by
   deterministic code.
3. **Inventory is an append-only ledger.** Balances are projections; quantity is never editable.
4. **State machines govern every entity**; illegal transitions are rejected by the workflow engine.
5. **Critical operations are idempotent** (Idempotency-Key header / idempotency_key field).
6. **Important operations emit events and audit records**; regulated records use
   cancel/void/supersede semantics, never destructive deletion.
7. **Email is simulated for V1** (notification records with status `CREATED` → `SIMULATED_SENT`).
8. **MongoDB transactional outbox** for event delivery (no external broker required for V1).

## Workspaces (UI)

AI Command Center · Customers & Sales · Supply Chain · Vendors & Procurement · Warehouse &
Inventory · Quality · Plant / Production · Pharmacy · Logistics · Finance & Governance.

## Technology stack

React + Vite (frontend) · Python/FastAPI modular monolith (backend) · MongoDB · Redis (optional
dev fallback) · S3/MinIO object storage (GridFS fallback) · OpenAI API (offline deterministic
fallback) · JWT/OAuth2 + RBAC + policies · Docker Compose deployment · structured logs and
health/metrics endpoints.
