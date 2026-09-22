# Agent ↔ Human Matrix (Frozen)

## Automation targets (design goals shown in the AI Command Center)

```
Transactions processed today:   12,482
Automatically completed:        12,391   (99.3%)
Human decisions required:           91
```

## What AI decides autonomously (deterministic policy + agent investigation)

| Area | Autonomous |
|---|---|
| PR/PO approval | Within policy: approved vendor, contract price, within amount limit, SoD clean |
| Invoice matching | Clean 2/3/4-way match within tolerance |
| Sourcing | Bid collection, comparison, scoring, award recommendation drafting |
| Supply planning | Replenishment proposal: transfer vs produce vs buy |
| Warehouse | Receiving proposals, putaway bins, pick waves, FEFO allocation |
| Document AI | Classification, extraction, master-data matching, validation |
| Logistics | Carrier selection, route/ETA, rescheduling |
| Finance investigation | Mismatch investigation (check QA records, request credit note), cash application proposals |
| Customer service | Order status, ETA answers, proactive notifications |
| Quality prep | Deviation/CAPA draft assembly, evidence collection, OOS prelim analysis |
| Recall execution | Global block, locating stock/customers, task creation (blocking is automatic) |

## What humans MUST decide (Human Decision Queue categories)

| Category | Examples |
|---|---|
| Physical work | Receiving, putaway, picking, packing, dispatch, dispensing, weighing |
| Clinical authority | Prescription approve/reject/clarify (registered pharmacist only) |
| QA authority | Batch release/hold, incoming disposition, recall closure, OOS final |
| Financial authority | High-value PO approval (over limit), payment authorization, credit notes |
| Strategic | Strategic sourcing award, new strategic vendor, pricing exceptions |
| Security/fraud | Bank-detail change of vendor, suspicious invoice, fraud alerts |
| Regulatory exception | Licence expiry, blocked vendor, compliance deviations |
| Unresolved exceptions | Anything the self-resolution loop cannot close |

## Self-resolution loop before every human escalation

```
Problem → Agent investigates → retry/alternate extraction → check master data
→ check related workflow → ask another permitted tool/agent
→ request supplier/customer correction → apply deterministic policy
→ still unresolved? → Human Decision Queue (with assembled evidence)
```

## Escalation record (approvals collection)

Every queue item carries: category, entity link, evidence bundle (documents, AI analysis, policy
evaluation, related records), proposed action, options, expiry, and decision audit. Decisions are
made with one click: **Approve / Reject / Hold / Clarify** — always with reason.
