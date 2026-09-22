# Master Architecture (Frozen)

## 1. System shape

One application. A **modular monolith** backend with 18 domains, a horizontal platform layer,
and an AI operations layer. Ten UI workspaces. External actors (customer, patient, hospital,
distributor, vendor, manufacturer, carrier, bank) connect through APIs and the Vendor Portal.

## 2. Layers

```
┌──────────────────────────────────────────────────────────────┐
│ FRONTEND (React + Vite)                                      │
│ AI Command Center | 10 workspaces | Human Decision Queue     │
│ Workflow Viewer | Vendor Portal                              │
├──────────────────────────────────────────────────────────────┤
│ API (FastAPI)  auth, users, 18 domain routers, portal, ws    │
├──────────────────────────────────────────────────────────────┤
│ AI OPERATIONS LAYER                                          │
│ Supervisor Agent · Domain Agents · Document Intelligence     │
│ Self-Resolution · Human Exception Center · Analytics         │
├──────────────────────────────────────────────────────────────┤
│ PLATFORM SERVICES                                            │
│ Workflow Engine | Policy Engine | Approval Engine            │
│ Event Engine + Outbox | Audit Engine | Notifications         │
│ Agent Tool Gateway | AI Gateway | Document AI | Storage      │
├──────────────────────────────────────────────────────────────┤
│ MongoDB (+indexes, tx, idempotency, ledgers) | Redis | S3    │
└──────────────────────────────────────────────────────────────┘
```

## 3. Domains (backend/app/domains)

masters · crm · sales · planning · vendors · sourcing · procurement · logistics · warehouse ·
inventory · qc · qa · production · pharmacy · finance · reverse · safety · compliance.

Each domain package contains: `models.py` (Pydantic), `service.py` (business logic + state
machines + events + audit), `router.py` (HTTP API), and where applicable `events.py` (event
handlers for cross-domain interlinks).

## 4. Platform services (backend/app/core)

| Service | Responsibility |
|---|---|
| `config.py` | Settings via environment with dev-safe defaults |
| `database.py` | Mongo client, indexes, GridFS bucket, tx helper |
| `redis_client.py` | Redis or in-memory fallback for cache/locks/counters |
| `security.py` | JWT, password hashing, current-user dependency |
| `rbac.py` | Role/permission checks, SoD enforcement, dependency factories |
| `events.py` | Event engine: publish → outbox → dispatch to local handlers (at-least-once) |
| `workflow.py` | State machines, transition validation, timeline persistence |
| `approvals.py` | Human Decision Queue: create/decide/approve/reject with evidence |
| `policies.py` | Policy rules + evaluation (amount limits, roles, SoD, flags) |
| `audit.py` | Append-only audit events (actor, states, policy, reason) |
| `idempotency.py` | Idempotency-Key support for critical writes |
| `notifications.py` | Notification records (Email = static/simulated for V1) |
| `storage.py` | S3/MinIO or GridFS/local fallback for documents |
| `ai_gateway.py` | OpenAI gateway with offline deterministic fallback |
| `docai.py` | Shared Document Intelligence pipeline (classify→extract→validate→route) |
| `logging.py` | Structured JSON logging, request IDs |
| `errors.py` | Domain exception hierarchy + handlers |

## 5. Resilience & degradation model

| Dependency | Unavailable → behaviour |
|---|---|
| Redis | In-process cache/locks; counters via Mongo `find_one_and_update` |
| S3/MinIO | GridFS in MongoDB; then local `uploads/` directory |
| OpenAI | Deterministic offline extraction heuristics (regex/keyword), flagged `fallback=true` |
| Mongo outbox pump | Events dispatched inline within the same transaction boundary (best-effort local handlers + outbox rows retained for replay) |

## 6. Cross-cutting integrity rules

- Optimistic concurrency (`version` field) on critical collections.
- Idempotency keys on: PR/PO/GRN/invoice/payment/dispense/return/recall creation and state changes.
- Append-only: `inventory_movements`, `audit_events`, `gl_entries`, `agent_tool_calls`.
- Regulated records (batch records, specifications, contracts, QA dispositions) are versioned and
  superseded — never silently modified or deleted.
- Every state transition: validate → persist → event → audit (same service transaction).
