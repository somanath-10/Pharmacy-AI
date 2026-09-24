import React, { useEffect, useState } from "react";
import { api } from "../api";
import { Topbar } from "../App";
import { Badge, Table, Money, When, useToast, CountTabs, Drawer, Stepper, Field, Kpi, Modal } from "../ui";

const TABS = ["Leads", "Opportunities", "Quotations", "Sales Orders", "Customer 360", "POS"];

export default function SalesWorkspace() {
  const [tab, setTab] = useState("Sales Orders");
  const [leads, setLeads] = useState([]);
  const [quotes, setQuotes] = useState([]);
  const [orders, setOrders] = useState([]);
  const [err, setErr] = useState("");
  const [detail, setDetail] = useState(null);
  const [c360Id, setC360Id] = useState("");
  const [c360, setC360] = useState(null);
  const [cancelFor, setCancelFor] = useState(null);
  const [cancelReason, setCancelReason] = useState("");
  const { toast, toastHost } = useToast();

  const load = async () => {
    try {
      const [l, q, o] = await Promise.all([
        api("/api/crm/leads").catch(() => []),
        api("/api/sales/quotations").catch(() => []),
        api("/api/sales/orders"),
      ]);
      setLeads(Array.isArray(l) ? l : l.items || []);
      setQuotes(Array.isArray(q) ? q : q.items || []);
      setOrders(Array.isArray(o) ? o : o.items || []);
      setErr("");
    } catch (e) { setErr(e.message); }
  };
  useEffect(() => { load(); const t = setInterval(load, 15000); return () => clearInterval(t); }, []);

  const act = async (path, body = {}, label = "Done") => {
    try { await api(path, { method: "POST", body }); toast(label, "ok"); load(); }
    catch (e) { toast(e.message, "err"); }
  };

  const openC360 = async (id) => {
    setC360Id(id);
    try { setC360(await api(`/api/crm/customers/${encodeURIComponent(id)}/360`)); }
    catch (e) { toast(e.message, "err"); }
  };

  const openOrders = orders.filter((o) => !["CLOSED", "CANCELLED"].includes(o.status));
  const backordered = orders.filter((o) => (o.backorders || []).length ||
    (o.lines || []).some((l) => l.backorder_qty));

  return (
    <div>
      {toastHost}
      <Topbar title="Customers & Sales"
              sub="Lead → opportunity → RFQ → quotation → customer PO → sales order → fulfillment → O2C" />
      {err && <div className="error-box mb">{err}</div>}

      <div className="grid kpi-4 mb">
        <Kpi ico="📄" label="Open orders" value={openOrders.length} tone="blue" />
        <Kpi ico="⏳" label="Backordered" value={backordered.length} tone={backordered.length ? "orange" : "green"} />
        <Kpi ico="📝" label="Open quotations" value={quotes.filter((q) => ["DRAFT", "SENT"].includes(q.status)).length} tone="purple" />
        <Kpi ico="🎯" label="Open opportunities" value={leads.filter((l) => l.status === "CONVERTED" || l.opportunity).length} tone="teal" />
      </div>

      <div className="row mb wrap">
        <CountTabs tabs={TABS.map((t) => ({
          label: t,
          count: t === "Leads" ? leads.filter((l) => l.status !== "CONVERTED").length
            : t === "Opportunities" ? leads.filter((l) => l.status === "CONVERTED" || l.opportunity).length
            : t === "Quotations" ? quotes.filter((q) => ["DRAFT", "SENT"].includes(q.status)).length
            : t === "Sales Orders" ? openOrders.length : null,
        }))} active={tab} onChange={setTab} />
        <div className="spacer" />
        <button className="btn ghost"
                onClick={() => act("/api/agents/run/sales-agent", { goal: "morning_pipeline_review" }, "Sales agent run started")}>
          🤖 Run Sales Agent
        </button>
      </div>

      <div className="tab-panel" key={tab}>
        {tab === "Leads" && (
          <div className="card">
            <h3>Leads & inquiries (AI-scored)</h3>
            <div className="card-sub">Capture → enrich → score — automatic; humans convert</div>
            <Table rows={leads} empty="No leads captured yet"
              columns={[
                { key: "lead_id", label: "Lead", render: (r) => <span className="mono">{r.lead_id || r.id}</span> },
                { key: "company_name", label: "Company", render: (r) => r.company_name || r.company },
                { key: "source", label: "Source", render: (r) => <Badge>{(r.source || "web").toUpperCase()}</Badge> },
                { key: "score", label: "AI score", render: (r) => <b style={{ color: (r.score || 0) > 70 ? "var(--green-deep)" : "var(--orange-deep)" }}>{r.score ?? "—"}</b> },
                { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                { key: "act", label: "", render: (r) => r.status !== "CONVERTED" ? (
                  <button className="btn primary sm" onClick={() =>
                    act(`/api/crm/leads/${r.lead_id || r.id}/convert`, {}, "Converted to opportunity")}>Convert</button>
                ) : null },
              ]} />
          </div>
        )}

        {tab === "Opportunities" && (
          <div className="card">
            <h3>Opportunities</h3>
            <div className="card-sub">DISCOVERY → RFQ → quotation → WON (customer created) / LOST</div>
            <Table rows={leads.filter((l) => l.status === "CONVERTED" || l.opportunity)} empty="No open opportunities"
              columns={[
                { key: "lead_id", label: "Lead", render: (r) => <span className="mono">{r.lead_id || r.id}</span> },
                { key: "company_name", label: "Company", render: (r) => r.company_name || r.company },
                { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
              ]} />
          </div>
        )}

        {tab === "Quotations" && (
          <div className="card">
            <h3>Quotations</h3>
            <div className="card-sub">Discount policy enforced by the policy engine; over-limit → decision queue</div>
            <Table rows={quotes} empty="No quotations yet"
              columns={[
                { key: "quote_id", label: "Quote", render: (r) => <span className="mono">{r.quote_id}</span> },
                { key: "customer_id", label: "Customer", render: (r) => <span className="mono">{r.customer_id}</span> },
                { key: "total_amount", label: "Value", render: (r) => <Money value={r.total_amount || r.subtotal} /> },
                { key: "discount_pct", label: "Disc %" },
                { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                { key: "created_at", label: "Created", render: (r) => <span className="small">{When(r.created_at)}</span> },
                { key: "act", label: "", render: (r) => r.status === "DRAFT" ? (
                  <button className="btn primary sm" onClick={() =>
                    act(`/api/sales/quotations/${r.quote_id}/send`, {}, "Quotation sent")}>Send</button>
                ) : null },
              ]} />
          </div>
        )}

        {tab === "Sales Orders" && (
          <div className="card">
            <h3>Sales orders (O2C)</h3>
            <div className="card-sub">Validation → stock check → reservation → pick/pack → ship → deliver → invoice → cash. Click a row to drive it.</div>
            <Table rows={orders} onRow={setDetail} empty="No sales orders yet"
              columns={[
                { key: "order_id", label: "Order", render: (r) => <span className="mono">{r.order_id}</span> },
                { key: "customer_id", label: "Customer", render: (r) => (
                  <a className="link small mono" onClick={(e) => { e.stopPropagation(); openC360(r.customer_id); }}>
                    {r.customer_id}
                  </a>) },
                { key: "total_amount", label: "Value", render: (r) => <Money value={r.total_amount} /> },
                { key: "backorder", label: "Backorder", render: (r) => (r.backorders || []).length ||
                    (r.lines || []).some((l) => l.backorder_qty)
                    ? <span className="badge orange">partial</span> : <span className="muted">—</span> },
                { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                { key: "rx", label: "Rx", render: (r) => r.prescription_required ? <span className="badge purple">Rx linked</span> : <span className="muted">—</span> },
                { key: "created_at", label: "Created", render: (r) => <span className="small">{When(r.created_at)}</span> },
                { key: "open", label: "", render: () => <span className="link small">Open →</span> },
              ]} />
          </div>
        )}

        {tab === "Customer 360" && (
          <div className="card">
            <h3>Customer 360</h3>
            <div className="card-sub">Profile, commercial history and derived metrics (LTV, open AR, OTIF) — all real data</div>
            <div className="row mb wrap">
              <input className="input mono" style={{ width: 220 }} placeholder="Customer code e.g. CUST-00001"
                     value={c360Id} onChange={(e) => setC360Id(e.target.value)} />
              <button className="btn primary" onClick={() => openC360(c360Id.trim())} disabled={!c360Id.trim()}>Load</button>
            </div>
            {c360 && <Customer360Body c={c360} onOpenOrder={(id) => {
              const o = c360.orders.find((x) => x.order_id === id);
              if (o) { setDetail(o); }
            }} />}
        </div>
        )}

        {tab === "POS" && (
          <div className="card">
            <h3>POS / counter sales</h3>
            <div className="card-sub">Prescription branch: Doc AI → compliance rules → pharmacist authority → dispense</div>
            <PosForm onDone={load} />
          </div>
        )}
      </div>

      {/* order drawer */}
      <Drawer title={detail ? `Order ${detail.order_id}` : ""} sub="Order-to-cash lifecycle"
              open={!!detail} onClose={() => setDetail(null)} wide>
        {detail && <OrderBody order={detail} act={act} onCancel={() => { setCancelFor(detail); setCancelReason(""); }} />}
      </Drawer>

      {/* customer 360 drawer */}
      <Drawer title={c360 ? `Customer ${c360.profile.code}` : ""} sub="360° customer view"
              open={!!c360 && tab !== "Customer 360"} onClose={() => setC360(null)} wide>
        {c360 && <Customer360Body c={c360} onOpenOrder={(id) => {
          const o = c360.orders.find((x) => x.order_id === id);
          if (o) setDetail(o);
        }} />}
      </Drawer>

      {/* cancel modal */}
      <Modal title="Cancel sales order" sub={cancelFor ? `${cancelFor.order_id} · reservations will be released` : ""}
             open={!!cancelFor} onClose={() => setCancelFor(null)}
             footer={
               <>
                 <button className="btn ghost" onClick={() => setCancelFor(null)}>Keep order</button>
                 <div className="spacer" />
                 <button className="btn danger" disabled={!cancelReason.trim()} onClick={() => {
                   act(`/api/sales/orders/${cancelFor.order_id}/cancel`, { reason: cancelReason.trim() },
                       "Order cancelled — stock released");
                   setCancelFor(null);
                 }}>Cancel order</button>
               </>
             }>
        <div className="error-box"><span>⚠️</span><span>Any active stock reservations are released back to AVAILABLE immediately.</span></div>
        <Field label="Cancellation reason" hint="Recorded in the audit trail">
          <textarea rows={2} value={cancelReason} placeholder="e.g. customer restructured their demand"
                    onChange={(e) => setCancelReason(e.target.value)} />
        </Field>
      </Modal>
    </div>
  );
}

function Customer360Body({ c, onOpenOrder }) {
  const m = c.metrics || {};
  return (
    <div>
      <div className="row mb wrap">
        <span className="badge blue">{c.profile.type || "CUSTOMER"}</span>
        {c.profile.pricing_tier && <span className="badge purple">tier {c.profile.pricing_tier}</span>}
        <div className="stat-line" style={{ margin: 0 }}>
          <div className="st-b"><small>Lifetime value</small><b><Money value={m.lifetime_value} /></b></div>
          <div className="st-b"><small>Open AR</small><b><Money value={m.open_ar} /></b></div>
          <div className="st-b"><small>Orders</small><b>{m.orders_count}</b></div>
          <div className="st-b"><small>OTIF</small><b>{m.otif_pct != null ? `${m.otif_pct}%` : "—"}</b></div>
          <div className="st-b"><small>Open returns</small><b>{m.open_returns}</b></div>
        </div>
      </div>

      <div className="section-title">Orders</div>
      <Table rows={c.orders} empty="No orders"
        columns={[
          { key: "order_id", label: "Order", render: (r) => (
            <a className="link mono small" onClick={() => onOpenOrder(r.order_id)}>{r.order_id}</a>) },
          { key: "total_amount", label: "Value", render: (r) => <Money value={r.total_amount} /> },
          { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
          { key: "created_at", label: "Date", render: (r) => <span className="small">{When(r.created_at)}</span> },
        ]} />

      <div className="grid cols-2 mt">
        <div>
          <div className="section-title">Invoices & payments</div>
          <Table rows={c.invoices} empty="No invoices"
            columns={[
              { key: "invoice_id", label: "Invoice", render: (r) => <span className="mono small">{r.invoice_id}</span> },
              { key: "total_amount", label: "Total", render: (r) => <Money value={r.total_amount} /> },
              { key: "balance_amount", label: "Balance", render: (r) => <Money value={r.balance_amount} /> },
              { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
            ]} />
        </div>
        <div>
          <div className="section-title">Shipments</div>
          <Table rows={c.shipments} empty="No shipments"
            columns={[
              { key: "shipment_id", label: "Shipment", render: (r) => <span className="mono small">{r.shipment_id}</span> },
              { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
              { key: "eta", label: "ETA", render: (r) => <span className="small">{When(r.eta)}</span> },
            ]} />
        </div>
      </div>

      <div className="grid cols-2 mt">
        <div>
          <div className="section-title">Returns</div>
          <Table rows={c.returns} empty="No returns"
            columns={[
              { key: "return_id", label: "Return", render: (r) => <span className="mono small">{r.return_id}</span> },
              { key: "reason", label: "Reason", render: (r) => <Badge>{r.reason}</Badge> },
              { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
            ]} />
        </div>
        <div>
          <div className="section-title">Quotations</div>
          <Table rows={c.quotations} empty="No quotations"
            columns={[
              { key: "quote_id", label: "Quote", render: (r) => <span className="mono small">{r.quote_id}</span> },
              { key: "subtotal", label: "Value", render: (r) => <Money value={r.total_amount || r.subtotal} /> },
              { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
            ]} />
        </div>
      </div>
    </div>
  );
}

function PosForm({ onDone }) {
  const [sku, setSku] = useState("");
  const [qty, setQty] = useState(1);
  const [rxText, setRxText] = useState("");
  const [busy, setBusy] = useState(false);
  const { toast, toastHost } = useToast();
  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      await api("/api/sales/pos", { method: "POST",
        body: { lines: [{ sku, quantity: Number(qty) }],
                prescription_ids: rxText ? [rxText] : [] } });
      toast("POS sale recorded", "ok"); setSku(""); setQty(1); setRxText(""); onDone();
    } catch (e2) { toast(e2.message, "err"); } finally { setBusy(false); }
  };
  return (
    <>
      {toastHost}
      <form onSubmit={submit}>
        <div className="form-row">
          <Field label="Product SKU">
            <input value={sku} onChange={(e) => setSku(e.target.value)} placeholder="PRD-00001" className="mono" required />
          </Field>
          <Field label="Quantity">
            <input type="number" min="1" value={qty} onChange={(e) => setQty(e.target.value)} />
          </Field>
        </div>
        <Field label="Prescription ID (if Rx product — create it in Pharmacy first)">
          <textarea rows={2} value={rxText} onChange={(e) => setRxText(e.target.value)}
                    placeholder={"RX-2026-00012"} />
        </Field>
        <button className="btn primary" disabled={busy}>{busy && <span className="spin" />} Record sale</button>
      </form>
    </>
  );
}

function OrderBody({ order, act, onCancel }) {
  const id = order.order_id;
  const st = order.status;
  const stages = ["DRAFT", "CONFIRMED", "ALLOCATED", "PICKING", "PACKED", "DISPATCHED", "DELIVERED", "INVOICED", "CLOSED"];
  const hasBackorder = (order.lines || []).some((l) => l.backorder_qty);
  return (
    <div>
      <div className="row mb wrap">
        <Badge>{st}</Badge>
        <span className="small muted">{order.customer_id}</span>
        <div className="spacer" />
        <Money value={order.total_amount} />
      </div>
      {order.credit_check && !order.credit_check.ok && (
        <div className="error-box"><span>💳</span><span>Credit check failed: {order.credit_check.reason}</span></div>
      )}
      <div className="section-title">Lifecycle</div>
      <Stepper steps={stages} current={st} />
      <div className="row mt wrap">
        {(st === "DRAFT" || st === "CREATED") &&
          <button className="btn primary sm" onClick={() => act(`/api/sales/orders/${id}/confirm`, {}, "Order confirmed")}>Confirm</button>}
        {st === "PENDING_RX" && <span className="badge purple">waiting for pharmacist</span>}
        {st === "CONFIRMED" && (
          <>
            <button className="btn primary sm" onClick={() => act(`/api/sales/orders/${id}/allocate`, {}, "Stock allocated (FEFO)")}>Allocate (full)</button>
            <button className="btn ghost sm" onClick={() => act(`/api/sales/orders/${id}/allocate-partial`, {}, "Partial allocation — shortages backordered")}>Allocate (allow partial)</button>
          </>
        )}
        {st === "ALLOCATED" &&
          <button className="btn primary sm" onClick={() => act("/api/warehouse/pick", { sales_order_id: id }, "Pick tasks created")}>Create pick tasks</button>}
        {st === "PICKING" && hasBackorder &&
          <span className="badge orange">partial — ship available, rest backordered</span>}
        {st === "PACKED" &&
          <button className="btn primary sm" onClick={() => act("/api/logistics/shipments", { sales_order_id: id, warehouse_id: "WH-MAIN" }, "Shipment planned")}>Plan shipment</button>}
        {st === "DELIVERED" &&
          <button className="btn primary sm" onClick={() => act(`/api/sales/orders/${id}/invoice`, {}, "Invoice issued")}>Invoice</button>}
        {!["CLOSED", "CANCELLED", "INVOICED"].includes(st) &&
          <button className="btn reject sm" onClick={onCancel}>Cancel</button>}
        <div className="spacer" />
        <a className="link small" href={`/workflows?entity=SALES_ORDER:${id}`}>Full workflow →</a>
      </div>

      {order.lines && (
        <>
          <hr className="divider" />
          <div className="section-title">Lines</div>
          <Table head={["SKU", "Ordered", "Shipped", "Backorder", "Unit price", "Amount"]}>
            {order.lines.map((l, i) => (
              <tr key={i}>
                <td className="mono">{l.sku}</td>
                <td>{l.quantity}</td>
                <td>{l.shipped_qty ?? 0}</td>
                <td>{l.backorder_qty ? <span className="badge orange">{l.backorder_qty}</span> : "—"}</td>
                <td><Money value={l.unit_price} /></td>
                <td><Money value={l.amount || l.quantity * l.unit_price} /></td>
              </tr>
            ))}
          </Table>
        </>
      )}
    </div>
  );
}
