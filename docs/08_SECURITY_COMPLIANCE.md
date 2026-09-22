# Security & Compliance (Frozen)

## Authentication & authorization

- JWT bearer (HS256; RS256-ready), access + refresh tokens, `sub` = user id, claims: roles,
  org/site context, vendor_id (for supplier portal users).
- Password hashing: bcrypt. Login lockout counters; all auth events audited.
- RBAC roles: SUPER_ADMIN, MANAGEMENT, PROCUREMENT, BUYER, VENDOR_MANAGER, WAREHOUSE, QA, QC,
  PLANT, PHARMACIST, SALES, FINANCE, LOGISTICS, COMPLIANCE, AUDITOR, SUPPLIER, CUSTOMER.
- Permission model: `resource:action` strings checked per route dependency; context conditions
  (site/warehouse/department/amount/state) evaluated by the policy engine.
- Segregation of duties (enforced, not advisory): vendor create ≠ vendor bank-change ≠ PO raise ≠
  invoice approval ≠ payment authorization. Same identity chain is blocked; agents included via
  the tool gateway (agent identity ≠ human approver identity).

## Agent safety

- Agents have their own principal (`type: AGENT`, id `procurement-agent`, …) with explicit tool
  allow-lists (`13_AGENT_TOOL_CONTRACTS.md`).
- Tool gateway pipeline: identity → tool permission → policy evaluation → approval engine (if
  governance requires) → domain API → business validation → Mongo transaction → domain event →
  audit. **No direct DB writes from agents.**
- Forbidden by construction: `set_inventory_quantity`, `approve_own_high_value_po`,
  `transfer_bank_funds`, `delete_audit_event` (absent from every tool registry).

## Regulated record semantics

- Append-only: inventory_movements, audit_events, gl_entries, agent_tool_calls, outbox_events,
  batch_record steps.
- Supersede/cancel/void for: batch records, specifications, contracts, POs (amendment),
  QA dispositions, master data changes (versioned).
- No destructive deletes via API for regulated entities; retention configured per class.

## Audit event model

```json
{
  "entity_type": "PURCHASE_ORDER",
  "entity_id": "PO-2026-00889",
  "action": "APPROVED",
  "actor": {"type": "AGENT", "id": "procurement-agent"},
  "previous_state": "PENDING_APPROVAL",
  "new_state": "APPROVED",
  "policy": "PO_AUTO_APPROVAL_V1",
  "reason": "Approved vendor, contract price, within limit",
  "timestamp": "2026-09-21T10:00:00Z"
}
```

## Compliance features

- Licence registry with expiry monitoring + `vendor.licence_expiry_warning` events.
- Controlled substance registers (Schedule H/H1/X, narcotics) with separate dispense ledgers.
- Recall blocking is immediate and automatic; reopening requires QA authority.
- Data protection: PII minimization in free text, hashed secrets, TLS at ingress, env-based secret
  management; healthcare records (prescriptions) access restricted to pharmacist/qa/audit roles.
