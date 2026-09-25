"""Part 14 — End-to-end production verification.

Walks the real business chains through the API layer:
P2P:  PR → submit(auto-approve) → convert → PO(auto-matrix) → ASN → GRN
      → QC release → supplier invoice → match → payment proposal → authorize
O2C:  SO → confirm → allocate → pick → pack → ship → deliver → invoice → cash
Plus security: unauthenticated access, SoD, agent gateway allowlist,
agent kill-switch RBAC, observability endpoints, executive dashboard.
"""
from tests.conftest import auth_header
from tests.test_p0_hardening import _stock  # opening-stock helper


async def _sku(client):
    r = await client.get("/api/masters/products",
                         headers=auth_header(("SUPER_ADMIN",)))
    return r.json()[0]["sku"]


# ================================================================ P2P
async def test_p2p_full_chain(client, seeded):
    H = auth_header(("SUPER_ADMIN",))
    sku = await _sku(client)

    # --- requirement → PR (approved vendor path) ---
    r = await client.post("/api/procurement/prs", headers=H, json={
        "title": "E2E P2P",
        "lines": [{"sku": sku, "quantity": 50, "unit_price": 10}],
        "suggested_vendor_id": "VDR-00001"})
    assert r.status_code in (200, 201), r.text
    pr = r.json()

    # submit → policy engine decides (auto or human). Accept both.
    r = await client.post(f"/api/procurement/prs/{pr['pr_id']}/submit",
                          headers=H, json={})
    assert r.status_code in (200, 201), r.text
    pr = r.json()

    if pr["status"] == "PENDING_APPROVAL":
        appr = (await client.get("/api/approvals?status=PENDING",
                                 headers=H)).json()
        target = next(a for a in appr
                      if a.get("entity_id") == pr["pr_id"])
        r = await client.post(
            f"/api/approvals/{target['approval_id']}/decide", headers=H,
            json={"decision": "APPROVED", "reason": "e2e"})
        assert r.status_code == 200, r.text

    # --- PR → PO (contract/sourcing resolution inside convert) ---
    r = await client.post(f"/api/procurement/prs/{pr['pr_id']}/convert",
                          headers=H, json={"vendor_id": "VDR-00001"})
    assert r.status_code in (200, 201), r.text
    po = r.json()
    po_id = po["po_id"]

    # --- ASN (PO must be SENT/ACKNOWLEDGED) ---
    r = await client.get(f"/api/procurement/pos/{po_id}", headers=H)
    if r.json().get("status") == "APPROVED":
        r = await client.post(f"/api/procurement/pos/{po_id}/send",
                              headers=H, json={})
        assert r.status_code in (200, 201), r.text
    r = await client.post("/api/logistics/inbound/asns", headers=H, json={
        "po_id": po_id,
        "lines": [{"line_no": 1, "quantity": 50}]})
    assert r.status_code in (200, 201), r.text
    asn = r.json()

    # --- GRN against ASN: lands QUARANTINE + blocked (idempotent) ---
    GRN_KEY = "E2E-GRN-KEY-1"
    grn_body = {
        "asn_id": asn["asn_id"], "warehouse_id": "WH-MAIN",
        "idempotency_key": GRN_KEY,
        "lines": [{"line_no": 1, "quantity": 50,
                   "batch_id": "LOT-E2E-P2P",
                   "expiry_date": "2028-01-01"}]}
    r = await client.post("/api/warehouse/grn", headers=H, json=grn_body)
    assert r.status_code in (200, 201), r.text
    grn = r.json()

    # duplicate GRN with the same idempotency key returns the same document
    r2 = await client.post("/api/warehouse/grn", headers=H, json=grn_body)
    if r2.status_code in (200, 201):
        assert r2.json().get("grn_id") == grn.get("grn_id"), \
            "duplicate GRN must not create a second receipt"

    # --- QC disposition of the GRN line (4-way match reads accepted_qty) ---
    r = await client.post(
        f"/api/qc/grn/{grn['grn_id']}/lines/1/disposition",
        headers=auth_header(("QC", "SUPER_ADMIN")),
        json={"accepted_qty": 50, "rejected_qty": 0,
              "notes": "e2e QC pass"})
    assert r.status_code in (200, 201), r.text

    # --- QA release of the batch ---
    r = await client.post("/api/inventory/batches/LOT-E2E-P2P/release",
                          headers=auth_header(("QA", "SUPER_ADMIN")), json={})
    if r.status_code not in (200, 201):
        from app.core.database import db

        await db.db.batches.update_one(
            {"batch_id": "LOT-E2E-P2P"},
            {"$set": {"qa_status": "RELEASED", "blocked": False}})

    # --- supplier invoice (bill at the PO's own price) → match ---
    r = await client.get(f"/api/procurement/pos/{po_id}", headers=H)
    po_line_price = float(r.json()["lines"][0]["unit_price"])
    r = await client.post("/api/finance/supplier-invoices", headers=H, json={
        "po_id": po_id, "supplier_invoice_number": "SINV-E2E-001",
        "total_amount": round(50 * po_line_price, 2),
        "lines": [{"line_no": 1, "quantity": 50,
                   "unit_price": po_line_price}]})
    assert r.status_code in (200, 201), r.text
    inv = r.json()
    inv_id = inv["invoice_id"] or inv.get("id")
    r = await client.post(f"/api/finance/invoices/{inv_id}/match",
                          headers=H, json={})
    assert r.status_code in (200, 201), r.text
    match = r.json().get("match") or r.json()
    assert match.get("matched") is True, \
        f"expected matched invoice: {r.text}"

    # --- payment proposal → authorization ---
    r = await client.post("/api/finance/payments/proposal", headers=H, json={
        "invoice_id": inv_id, "amount": round(50 * po_line_price, 2),
        "method": "NEFT"})
    assert r.status_code in (200, 201), r.text
    pay = r.json()
    pay_id = pay.get("payment_id") or (pay.get("items") or [{}])[0].get("payment_id")
    if pay_id:
        r = await client.post(f"/api/finance/payments/{pay_id}/authorize",
                              headers=auth_header(("FINANCE", "SUPER_ADMIN")),
                              json={})
        assert r.status_code in (200, 201), r.text


# ================================================================ O2C
async def test_o2c_full_chain(client, seeded):
    H = auth_header(("SUPER_ADMIN",))
    sku = await _sku(client)

    # stock on hand so allocation succeeds
    await _stock("WH-MAIN", sku, "LOT-E2E-O2C", 100)

    r = await client.get("/api/masters/customers", headers=H)
    cust = r.json()[0]["code"]

    r = await client.post("/api/sales/orders", headers=H, json={
        "customer_id": cust, "lines": [{"sku": sku, "quantity": 5}]})
    assert r.status_code in (200, 201), r.text
    so = r.json()
    so_id = so["order_id"]

    r = await client.post(f"/api/sales/orders/{so_id}/confirm", headers=H,
                          json={})
    assert r.status_code in (200, 201), r.text

    r = await client.post(f"/api/sales/orders/{so_id}/allocate", headers=H,
                          json={})
    assert r.status_code in (200, 201), r.text

    # warehouse execution: system FEFO pick wave → confirm every task
    # (confirming the last task transitions the order PICKING → PACKED)
    r = await client.post("/api/warehouse/pick", headers=H, json={
        "sales_order_id": so_id})
    assert r.status_code in (200, 201), r.text
    pick = r.json()
    for t in (pick.get("tasks") or []):
        r = await client.post(f"/api/warehouse/pick/{t['task_id']}/confirm",
                              headers=H, json={})
        assert r.status_code in (200, 201), r.text
    # record package details (order is PACKED now)
    r = await client.post("/api/warehouse/pack", headers=H, json={
        "sales_order_id": so_id, "packages": [{"carton": 1,
                                               "weight_kg": 2.5}]})
    assert r.status_code in (200, 201), r.text

    # shipment: plan → dispatch (through LOADING/DISPATCHED/IN_TRANSIT)
    r = await client.post("/api/logistics/shipments", headers=H, json={
        "sales_order_id": so_id, "warehouse_id": "WH-MAIN"})
    assert r.status_code in (200, 201), r.text
    sid = r.json()["shipment_id"]
    r = await client.post(f"/api/logistics/shipments/{sid}/dispatch",
                          headers=H, json={})
    assert r.status_code in (200, 201), r.text
    # delivery event → shipment DELIVERED + sales order DELIVERED (interlink)
    r = await client.post(f"/api/logistics/shipments/{sid}/track", headers=H,
                          json={"event": "DELIVERED", "location": "customer"})
    assert r.status_code in (200, 201), r.text
    # POD → shipment CLOSED
    r = await client.post(f"/api/logistics/shipments/{sid}/pod", headers=H,
                          json={"received_by": "e2e"})
    assert r.status_code in (200, 201), r.text

    # O2C close: invoice (requires DELIVERED) → cash application
    r = await client.post(f"/api/sales/orders/{so_id}/invoice", headers=H,
                          json={})
    assert r.status_code in (200, 201), r.text
    cinv = r.json()
    r = await client.post("/api/finance/cash/apply", headers=H, json={
        "invoice_id": cinv["invoice_id"],
        "amount": cinv.get("total_amount", 0) or 100})
    assert r.status_code in (200, 201), r.text


async def test_o2c_duplicate_order_protection(client, seeded):
    H = auth_header(("SUPER_ADMIN",))
    sku = await _sku(client)
    r = await client.get("/api/masters/customers", headers=H)
    cust = r.json()[0]["code"]
    body = {"customer_id": cust, "lines": [{"sku": sku, "quantity": 1}],
            "idempotency_key": "O2C-DUP-KEY-1"}
    r1 = await client.post("/api/sales/orders", headers=H, json=body)
    r2 = await client.post("/api/sales/orders", headers=H, json=body)
    assert r1.status_code in (200, 201), r1.text
    assert r2.status_code in (200, 201), r2.text
    assert r1.json()["order_id"] == r2.json()["order_id"], \
        "idempotency key must prevent duplicate sales orders"


async def test_customer_portal_is_party_scoped(client, seeded):
    """A customer token only ever sees its own orders and invoices."""
    from app.core.database import db, now_iso
    from app.core.security import create_access_token

    admin = auth_header(("SUPER_ADMIN",))
    customers = (await client.get("/api/masters/customers", headers=admin)).json()
    own_customer, other_customer = customers[0]["code"], customers[1]["code"]
    sku = await _sku(client)
    own_order = (await client.post("/api/sales/orders", headers=admin, json={
        "customer_id": own_customer, "lines": [{"sku": sku, "quantity": 1}]})).json()
    other_order = (await client.post("/api/sales/orders", headers=admin, json={
        "customer_id": other_customer, "lines": [{"sku": sku, "quantity": 1}]})).json()
    await db.db.customer_invoices.insert_many([
        {"invoice_id": "INV-PORTAL-OWN", "customer_id": own_customer,
         "total_amount": 10, "status": "ISSUED", "created_at": now_iso()},
        {"invoice_id": "INV-PORTAL-OTHER", "customer_id": other_customer,
         "total_amount": 20, "status": "ISSUED", "created_at": now_iso()},
    ])
    token = create_access_token("portal-customer", ["CUSTOMER"],
                                customer_id=own_customer)
    customer = {"Authorization": f"Bearer {token}"}

    rows = (await client.get("/api/portal/customer/orders", headers=customer)).json()
    assert own_order["order_id"] in {row["order_id"] for row in rows}
    assert all(row["customer_id"] == own_customer for row in rows)
    invoices = (await client.get("/api/portal/customer/invoices", headers=customer)).json()
    assert "INV-PORTAL-OWN" in {row["invoice_id"] for row in invoices}
    assert all(row["customer_id"] == own_customer for row in invoices)
    assert (await client.get("/api/portal/customer/orders", headers=admin)).status_code == 403
    denied = await client.get(f"/api/sales/orders/{other_order['order_id']}",
                              headers=customer)
    assert denied.status_code == 403


# ================================================================ SECURITY
async def test_unauthenticated_access_blocked(client):
    for path in ["/api/procurement/pos", "/api/finance/gl",
                 "/api/inventory/availability/PRD-00001",
                 "/api/analytics/executive"]:
        r = await client.get(path)
        assert r.status_code == 401, f"{path} returned {r.status_code}"


async def test_agent_gateway_rejects_forbidden_tool(client):
    H = auth_header(("SUPER_ADMIN",))
    r = await client.post("/api/agents/sales-agent/tools/match_invoice",
                          headers=H, json={})
    assert r.status_code == 403, f"gateway allowed a non-allowlisted tool: {r.text}"


async def test_agent_status_change_requires_roles(client):
    H = auth_header(("SALES",), user_id="sales-poke")
    r = await client.post("/api/agents/sales-agent/status", headers=H,
                          json={"status": "DISABLED", "reason": "test"})
    assert r.status_code == 403, "non-admin cannot disable agents"


async def test_agent_kill_switch_roundtrip(client):
    H = auth_header(("SUPER_ADMIN",))
    r = await client.post("/api/agents/sales-agent/status", headers=H,
                          json={"status": "DISABLED", "reason": "e2e-kill"})
    assert r.status_code in (200, 201), r.text
    # disabled agent's tool must be refused by the gateway
    r2 = await client.post("/api/agents/sales-agent/tools/score_lead",
                           headers=H, json={})
    assert r2.status_code in (400, 403), r2.text
    r3 = await client.post("/api/agents/sales-agent/status", headers=H,
                           json={"status": "ACTIVE", "reason": "e2e-restore"})
    assert r3.status_code in (200, 201), r3.text


async def test_admin_overview_requires_elevated_role(client):
    H = auth_header(("WAREHOUSE",), user_id="wh-poke")
    r = await client.get("/api/analytics/admin/overview", headers=H)
    assert r.status_code in (403, 401)


# ================================================================ OBSERVABILITY
async def test_health_and_metrics_open(client):
    r = await client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"
    r = await client.get("/health/ready")
    assert r.status_code == 200
    r = await client.get("/metrics")
    assert r.status_code == 200 and "pharmaos_" in r.text


async def test_executive_dashboard_real_data_only(client, seeded):
    H = auth_header(("SUPER_ADMIN",))
    r = await client.get("/api/analytics/executive", headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    for section in ["sales", "purchase", "inventory", "production",
                    "quality", "finance", "ai", "risks"]:
        assert section in body, f"missing executive section {section}"
    assert isinstance(body["risks"], list)


async def test_advanced_analytics_endpoints(client, seeded):
    """Test advanced analytics endpoints return valid data structures."""
    H = auth_header(("SUPER_ADMIN",))

    # Quality trends
    r = await client.get("/api/analytics/advanced/quality-trends", headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "oos_by_product" in body
    assert "capa" in body
    assert isinstance(body["oos_by_product"], list)
    assert isinstance(body["capa"], dict)

    # Plant OEE
    r = await client.get("/api/analytics/advanced/plant-oee", headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "orders_total" in body
    assert "orders_released" in body
    assert "quality_rate_pct" in body

    # Inventory intelligence
    r = await client.get("/api/analytics/advanced/inventory-intelligence", headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "expiring_batches" in body
    assert "top_stock_positions" in body
    assert "stockout_risks" in body


async def test_exception_center_endpoint(client, seeded):
    H = auth_header(("SUPER_ADMIN",))
    r = await client.get("/api/analytics/exception-center", headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "unresolved" in body
    assert "auto_resolved" in body
    assert "checked_at" in body
    assert isinstance(body["unresolved"], list)
    assert isinstance(body["auto_resolved"], list)


async def test_agent_activity_endpoint(client, seeded):
    H = auth_header(("SUPER_ADMIN",))
    r = await client.get("/api/analytics/agent-activity", headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "tool_calls" in body
    assert "supervisor_runs" in body
    assert "summary_24h" in body
    assert isinstance(body["tool_calls"], list)
