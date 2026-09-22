# Agent Tool Contracts (Frozen)

## Gateway pipeline (agents/gateway.py)

```
Agent(principal) → ToolPermissionCheck(allow-list) → PolicyEngine → ApprovalEngine?
→ Domain API function → Business validation → Mongo transaction → Domain event → Audit
```

Every call is recorded in `agent_tool_calls` (agent, tool, args digest, result, duration,
policy result, approval link). Failures increment agent error counters; repeated failure
escalates to the Human Decision Queue.

## Supervisor Agent

Routing & retry over domain agents; self-resolution loop; cross-domain coordination (e.g.,
invoice mismatch → ask QA record → request supplier correction → re-match); heartbeat for the
Command Center; escalation taxonomy enforcement (physical, clinical, QA, financial, strategic,
security, regulatory, unresolved). Entry point: `POST /api/agents/supervisor/tick`.

## Domain agents & tools (implemented)

| Agent | Tools (implemented) |
|---|---|
| Procurement Agent | get_inventory · get_open_purchase_orders · get_active_contracts · create_draft_pr · create_rfq · create_draft_po · submit_po_for_approval |
| Supply Chain Agent | get_inventory_snapshot · compute_supply_plan · propose_transfer · propose_production · propose_purchase |
| Document AI Agent | classify_document · extract_document · match_document_to_masters |
| Finance Agent | get_invoice_match_detail · investigate_mismatch · request_credit_note · create_payment_proposal |
| Warehouse Agent | propose_putaway · create_pick_wave · get_warehouse_health |
| QA/QC Agent | assemble_batch_release_dossier · draft_deviation · get_oos_summary |
| Pharmacy Agent | match_prescription_to_master · draft_pharmacist_review |
| Logistics Agent | select_carrier · estimate_eta · reschedule_shipment |
| Sales Agent | score_lead · draft_quotation · confirm_order_document |
| Customer Service Agent | get_order_status · notify_customer |
| Compliance Agent | check_licence_expiries · flag_sod_violation |
| Analytics Agent | get_command_center_kpis |

## Explicitly forbidden (not present anywhere)

`set_inventory_quantity` · `approve_own_high_value_po` · `transfer_bank_funds` ·
`delete_audit_event` · any raw collection write/update/delete.

## Rules

- Tools return data or *drafts*; final persistence goes through domain APIs that enforce state
  machines, idempotency and audit.
- Approval-required tools create an approvals item and return its id; the agent never approves
  its own action (SoD).
- All agent traffic is auditable and replayable from `agent_runs` / `agent_tool_calls`.
