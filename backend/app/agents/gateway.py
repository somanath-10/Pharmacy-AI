"""Agent Tool Gateway — the ONLY path from AI to business actions.

Pipeline: identity → tool permission (allow-list) → policy engine → approval
engine (if governance requires) → domain API → validation → Mongo txn →
event → audit. Agents NEVER write to MongoDB directly.
"""
import functools
import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

from app.core.audit import audit
from app.core.database import db, now_iso
from app.core.errors import DomainError, PermissionDenied
from app.core.events import bus

log = logging.getLogger("pharmaos.agents")

AGENT_REGISTRY: Dict[str, Dict[str, Any]] = {}


def register_agent(agent_id: str, description: str, allowed_tools: list,
                   allowed_domains: list):
    AGENT_REGISTRY[agent_id] = {
        "agent_id": agent_id,
        "description": description,
        "allowed_tools": set(allowed_tools),
        "allowed_domains": allowed_domains,
        "status": "ACTIVE",
        "runs": 0,
        "errors": 0,
        "last_run_at": None,
    }
    return AGENT_REGISTRY[agent_id]


def agent_tool(domain: str, name: str, requires_approval_over: Optional[float] = None,
               description: str = ""):
    """Decorator that wraps a domain function as a governed agent tool."""

    def decorator(fn: Callable):
        @functools.wraps(fn)
        async def wrapper(agent_id: str, payload: Dict[str, Any],
                          actor: Optional[dict] = None,
                          meta: Optional[Dict[str, Any]] = None) -> Any:
            started = time.time()
            reg = AGENT_REGISTRY.get(agent_id)
            if not reg:
                raise PermissionDenied(f"Unknown agent {agent_id}")
            if reg["status"] != "ACTIVE":
                raise PermissionDenied(f"Agent {agent_id} disabled")
            if name not in reg["allowed_tools"]:
                raise PermissionDenied(
                    f"Agent {agent_id} not allowed tool {name}")
            actor = actor or {"type": "AGENT", "id": agent_id}
            call_digest = hashlib.sha256(
                json.dumps(payload, sort_keys=True, default=str).encode()
            ).hexdigest()[:16]

            call_doc = {
                "agent": agent_id,
                "tool": name,
                "domain": domain,
                "args_digest": call_digest,
                "args_preview": {k: str(v)[:100] for k, v in
                                 list(payload.items())[:8]},
                "status": "RUNNING",
                # Part 9 metadata: why the agent acted, with what model and
                # how confident; escalation_reason set when routed to human.
                "reason": (meta or {}).get("reason"),
                "confidence": (meta or {}).get("confidence"),
                "model": (meta or {}).get("model"),
                "escalation_reason": (meta or {}).get("escalation_reason"),
                "created_at": now_iso(),
            }
            res = await db.db.agent_tool_calls.insert_one(call_doc)
            call_id = str(res.inserted_id)
            call_doc["call_id"] = call_id  # stable string handle for updates

            try:
                # Approval gate: large financial actions require the queue
                if requires_approval_over is not None:
                    amount = float(payload.get("amount") or
                                   payload.get("total_amount") or 0)
                    if amount > requires_approval_over:
                        approval_id = await _create_tool_approval(
                            agent_id, name, payload, amount)
                        await _finish_call(call_id, "AWAITING_APPROVAL", started)
                        return {"status": "AWAITING_APPROVAL",
                                "approval_id": approval_id,
                                "tool": name, "amount": amount}
                result = await fn(payload=payload, actor=actor)
                await _finish_call(call_id, "SUCCESS", started,
                                   result_preview=_digest_result(result))
                reg["runs"] += 1
                reg["last_run_at"] = now_iso()
                await audit("AGENT_TOOL_CALL", call_id, name,
                            {"type": "AGENT", "id": agent_id},
                            details={"domain": domain,
                                     "args_digest": call_digest})
                return result
            except Exception as e:
                await _finish_call(call_id, "FAILED", started, error=str(e)[:300])
                reg["errors"] += 1
                if reg["errors"] >= 5:
                    reg["status"] = "DEGRADED"
                    await _escalate_agent_health(agent_id, reg)
                raise

        wrapper.tool_name = name
        wrapper.tool_domain = domain
        wrapper.tool_description = description or (fn.__doc__ or "").strip()
        return wrapper

    return decorator


async def _finish_call(call_id: str, status: str, started: float,
                       result_preview: Any = None, error: str = None):
    # call_id is the string form of the inserted ObjectId — query via _id with
    # a real ObjectId. A raw string here never matches, leaving the row stuck
    # in RUNNING forever.
    from bson import ObjectId

    q = {"_id": ObjectId(call_id)} if ObjectId.is_valid(call_id) else {"call_id": call_id}
    await db.db.agent_tool_calls.update_one(
        q,
        {"$set": {"status": status,
                  "result_preview": result_preview,
                  "error": error,
                  "duration_ms": int((time.time() - started) * 1000)}})


def _digest_result(result: Any) -> Any:
    if isinstance(result, dict):
        return {k: str(v)[:80] for k, v in list(result.items())[:8]}
    return str(result)[:200]


async def _create_tool_approval(agent_id: str, tool: str, payload: dict,
                                amount: float) -> str:
    from app.core.approvals import create_approval
    from app.core.rbac import MANAGEMENT_AUTHORITY_ROLES

    return await create_approval(
        category="FINANCIAL_AUTHORITY",
        title=f"Agent {agent_id} requests {tool} (amount {amount})",
        entity_type="AGENT_TOOL_CALL", entity_id=f"{agent_id}:{tool}",
        requested_by={"type": "AGENT", "id": agent_id},
        evidence={"payload": payload, "amount": amount},
        options=["APPROVE", "REJECT"],
        authority_roles=list(MANAGEMENT_AUTHORITY_ROLES),
    )


async def _escalate_agent_health(agent_id: str, reg: dict):
    from app.core.approvals import create_approval
    from app.core.rbac import MANAGEMENT_AUTHORITY_ROLES

    await create_approval(
        category="UNRESOLVED_EXCEPTION",
        title=f"Agent {agent_id} degraded (errors={reg['errors']})",
        entity_type="AGENT", entity_id=agent_id,
        requested_by={"type": "AGENT", "id": "supervisor-agent"},
        evidence={"registry": {k: v for k, v in reg.items()
                               if k != "allowed_tools"}},
        options=["APPROVE", "REJECT"],
        authority_roles=list(MANAGEMENT_AUTHORITY_ROLES),
    )


async def list_agents() -> list:
    out = []
    for aid, reg in AGENT_REGISTRY.items():
        out.append({**reg, "allowed_tools": sorted(reg["allowed_tools"])})
    return out


async def agent_status(agent_id: str) -> dict:
    """Full agent health snapshot: registry state + live call statistics."""
    reg = AGENT_REGISTRY.get(agent_id)
    if not reg:
        raise DomainError(f"Unknown agent {agent_id}")
    calls = await db.db.agent_tool_calls.count_documents({"agent": agent_id})
    failed = await db.db.agent_tool_calls.count_documents({"agent": agent_id,
                                                           "status": "FAILED"})
    running = await db.db.agent_tool_calls.count_documents(
        {"agent": agent_id, "status": "RUNNING"})
    return {**{k: v for k, v in reg.items() if k != "allowed_tools"},
            "total_tool_calls": calls, "failed_calls": failed,
            "running_calls": running,
            "allowed_tools": sorted(reg["allowed_tools"])}


async def set_agent_status(agent_id: str, status: str, actor: dict,
                           reason: str = "") -> dict:
    """Kill-switch: disable/pause/re-enable an agent at runtime.

    Disabled agents fail every tool call with PermissionDenied (checked in the
    gateway wrapper). The action itself is audited for the compliance trail.
    """
    if status not in {"ACTIVE", "DISABLED", "PAUSED"}:
        raise DomainError(f"Invalid agent status {status}")
    reg = AGENT_REGISTRY.get(agent_id)
    if not reg:
        raise DomainError(f"Unknown agent {agent_id}")
    previous = reg["status"]
    reg["status"] = status
    await audit("AGENT", agent_id, "STATUS_CHANGE", actor,
                previous_state=previous, new_state=status, reason=reason)
    await bus.publish("agent.status_changed",
                      {"agent_id": agent_id, "previous": previous,
                       "status": status, "reason": reason}, actor)
    return {"agent_id": agent_id, "status": status, "previous": previous}


async def reap_stale_running(max_age_minutes: int = 15) -> int:
    """Recover tool calls orphaned in RUNNING (process crash / missed finish).

    Such rows are terminal-ambiguous; mark them FAILED so nothing is stuck.
    """
    from datetime import timedelta

    cutoff = (datetime.now(timezone.utc)
              - timedelta(minutes=max_age_minutes)).isoformat()
    res = await db.db.agent_tool_calls.update_many(
        {"status": "RUNNING", "created_at": {"$lt": cutoff}},
        {"$set": {"status": "FAILED",
                  "error": "reaped: orphaned RUNNING call"}})
    return res.modified_count
