# UI / UX Blueprint (Frozen)

Ten workspaces + platform screens (React + Vite, dark/light, dense data grids):

1. **AI Command Center** (landing) — KPI cards (Active Agents, Open Orders, Vendor Approvals,
   Human Decisions, Plant Readiness, On-time Dispatch, Automation Rate, Exceptions), Live Inputs
   panel, AI Orchestration Core panel, Outputs & Actions panel, Department Health strip,
   Decision Queue table with one-click Approve/Reject.
2. **Customers & Sales** — leads pipeline, opportunities, inquiries, quotations, customer PO
   intake (upload PDF → extraction), sales orders, order timeline, POS mode.
3. **Supply Chain** — forecast vs actual, demand plan, MRP proposals (transfer/produce/buy),
   safety stock, expiry risk.
4. **Vendors & Procurement** — vendor lifecycle Kanban, Vendor 360, documents with expiries,
   performance scorecards, PR/PO lists with approval status, sourcing events (RFQ → auction →
   BRA → contract), Vendor Portal (supplier login).
5. **Warehouse & Inventory** — inbound schedule (ASN/gate/dock), receiving (GRN) screens,
   quarantine & QC status bins, putaway tasks, pick/pack waves, stock by batch/expiry (FEFO),
   cycle counts, transfer orders, ledger viewer.
6. **Quality** — QC queue (samples/tests/results/CoA), OOS/OOT, QA queue (incoming dispositions,
   batch release dossiers), deviations, CAPA, change control, controlled documents.
7. **Plant / Production** — plan vs schedule, production orders with stage timeline, eBMR viewer
   (immutable step log), dispensing/weighing checks, line clearance, equipment readiness board,
   yield reconciliation.
8. **Pharmacy** — prescription intake (upload), extraction review, pharmacist queue with
   approve/reject/clarify, dispensing station, controlled registers.
9. **Logistics** — inbound tracking (PO → ASN → ETA → dock), outbound dispatch board,
   carrier/vehicle assignment, tracking timeline, POD capture.
10. **Finance & Governance** — supplier invoice inbox (extraction + match status), 3/4-way match
    screens, AP aging & payment proposals, AR & cash application, reconciliation, GL explorer,
    write-offs, budgets. Governance: recalls, returns, compliance/licences, audit viewer,
    document library, administration (users/roles).

## Platform screens

- **Human Decision Queue** — category filters, evidence drawer (AI summary, documents, policy
  result, related entities), Approve/Reject/Hold/Clarify with reason; SLA countdown.
- **Workflow Viewer** — every entity: horizontal stage timeline with per-node actor
  (agent/user), timestamps, documents, reason, previous/next state, audit links; export.
- **Agent Ops** — agent list with status/heartbeat, runs, tool calls, success/failure, escalations.

## Visual language

Reference: "One Autonomous Enterprise Box" — light gradient background, white cards, blue
gradient hero band, pill status chips, green/red action buttons, breadcrumb stage strips with
numbered nodes, live "Live · Orchestrating in real time" badge. Implemented in `frontend/src`
with custom CSS (no heavy UI kit dependency).
