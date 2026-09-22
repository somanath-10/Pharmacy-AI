import React, { useEffect, useState } from "react";
import { api } from "../api";
import { Topbar } from "../App";
import { Badge, Table, Money, When, useToast } from "../ui";

const TABS = ["Leads", "Opportunities", "Quotations", "Sales Orders", "POS"];

export default function SalesWorkspace() {
  const [tab, setTab] = useState("Sales Orders");
  const [leads, setLeads] = useState([]);
  const [quotes, setQuotes] = useState([]);
  const [orders, setOrders] = useState([]);
  const [err, setErr] = useState("");
  const [detail, setDetail] = useState(null);
  const toast = useToast();

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
    try { await api(path, { method: "POST", body }); toast(label); load(); }
    catch (e) { toast(e.message, "err"); }
  };

  return (
    <div>
      <Topbar title="Customers & Sales"
              sub="Market → lead → opportunity → quotation → customer PO → sales order → O2C" />
      {err && <div className="error-box mb">{err}</div>}
      <div className="row mb">
        {TABS.map((t) => (
          <button key={t} className={`btn ${tab === t ? "primary" : "ghost"}`} onClick={() => setTab(t)}>{t}</button>
        ))}
        <div className="spacer" />
        <button className="btn ghost"
                onClick={() => act("/api/agents/run/sales-agent", { goal: "morning_pipeline_review" }, "Sales agent run started")}>
          🤖 Run Sales Agent
        </button>
      </div>

      {tab === "Leads" && (
        <div className="card">
          <h3>Leads & inquiries (AI-scored)</h3>
          <div className="card-sub">Lead capture → enrichment → scoring — automatic</div>
          <Table rows={leads} empty="No leads captured yet"
            columns={[
              { key: "lead_id", label: "Lead", render: (r) => <span className="mono">{r.lead_id || r.id}</span> },
              { key: "company", label: "Company" },
              { key: "source", label: "Source", render: (r) => <Badge value={(r.source || "web").toUpperCase()} /> },
              { key: "score", label: "AI score", render: (r) => <b style={{ color: (r.score || 0) > 70 ? "var(--green)" : "var(--orange)" }}>{r.score ?? "—"}</b> },
              { key: "status", label: "Status", render: (r) => <Badge value={r.status} /> },
            ]} />
        </div>
      )}

      {tab === "Opportunities" && (
        <div className="card">
          <h3>Opportunities</h3>
          <Table rows={leads.filter((l) => l.status === "CONVERTED" || l.opportunity)} empty="No open opportunities"
            columns={[
              { key: "lead_id", label: "Lead", render: (r) => <span className="mono">{r.lead_id || r.id}</span> },
              { key: "company", label: "Company" },
              { key: "status", label: "Status", render: (r) => <Badge value={r.status} /> },
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
              { key: "customer_id", label: "Customer" },
              { key: "subtotal", label: "Value", render: (r) => <Money value={r.total_amount || r.subtotal} /> },
              { key: "discount_pct", label: "Disc %" },
              { key: "status", label: "Status", render: (r) => <Badge value={r.status} /> },
              { key: "created_at", label: "Created", render: (r) => When(r.created_at) },
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
          <div className="card-sub">Credit check → reservation → pick/pack → ship → deliver → invoice → cash</div>
          <Table rows={orders} onRow={setDetail} empty="No sales orders yet"
            columns={[
              { key: "order_id", label: "Order", render: (r) => <span className="mono">{r.order_id}</span> },
              { key: "customer_id", label: "Customer" },
              { key: "total_amount", label: "Value", render: (r) => <Money value={r.total_amount} /> },
              { key: "status", label: "Status", render: (r) => <Badge value={r.status} /> },
              { key: "rx", label: "Rx", render: (r) => r.prescription_required ? <span className="badge purple">Rx linked</span> : "—" },
              { key: "created_at", label: "Created", render: (r) => When(r.created_at) },
            ]} />
        </div>
      )}

      {tab === "POS" && (
        <div className="card">
          <h3>POS / counter sales</h3>
          <div className="card-sub">Prescription branch: Doc AI → compliance rules → pharmacist authority → dispense</div>
          <PosForm onDone={load} />
        </div>
      )}

      {detail && <OrderDetail order={detail} onClose={() => setDetail(null)} reload={load} act={act} />}
    </div>
  );
}

function PosForm({ onDone }) {
  const [sku, setSku] = useState("");
  const [qty, setQty] = useState(1);
  const [rxText, setRxText] = useState("");
  const toast = useToast();
  const submit = async (e) => {
    e.preventDefault();
    try {
      await api("/api/sales/pos", { method: "POST",
        body: { lines: [{ sku, quantity: Number(qty) }],
                prescription_text: rxText || undefined } });
      toast("POS sale recorded"); setSku(""); setQty(1); setRxText(""); onDone();
    } catch (e2) { toast(e2.message, "err"); }
  };
  return (
    <form onSubmit={submit}>
      <div className="form-row">
        <div className="field"><label>Product SKU</label>
          <input value={sku} onChange={(e) => setSku(e.target.value)} placeholder="PRD-00001" className="mono" required /></div>
        <div className="field"><label>Quantity</label>
          <input type="number" min="1" value={qty} onChange={(e) => setQty(e.target.value)} /></div>
      </div>
      <div className="field"><label>Prescription text (if Rx product — pharmacist review required)</label>
        <textarea rows={2} value={rxText} onChange={(e) => setRxText(e.target.value)}
                  placeholder="Dr. Mehta\nTab Paracetamol 500mg 1-0-1 x 5 days" /></div>
      <button className="btn primary">Record sale</button>
    </form>
  );
}

function OrderDetail({ order, onClose, reload, act }) {
  const id = order.order_id;
  const st = order.status;
  const steps = [
    ["confirm", "Confirm", ["DRAFT", "CREATED"]],
    ["allocate", "Allocate (FEFO)", ["CONFIRMED"]],
  ];
  return (
    <div className="card mt">
      <div className="row">
        <h3>Order {id}</h3>
        <div className="spacer" />
        <Badge value={st} />
        <button className="btn ghost sm" onClick={onClose}>Close</button>
      </div>
      <div className="wf-flow mt">
        {["DRAFT", "CONFIRMED", "ALLOCATED", "PICKING", "PACKED", "DISPATCHED", "DELIVERED", "INVOICED", "CLOSED"].map((s, i) => (
          <React.Fragment key={s}>
            {i > 0 && <span className="wf-arrow">→</span>}
            <div className={`wf-node ${s === st ? "active" : ""}`}><b>{s}</b></div>
          </React.Fragment>
        ))}
      </div>
      <div className="row mt" style={{ flexWrap: "wrap" }}>
        {(st === "DRAFT" || st === "CREATED") &&
          <button className="btn primary sm" onClick={() => act(`/api/sales/orders/${id}/confirm`, {}, "Order confirmed")}>Confirm</button>}
        {st === "CONFIRMED" &&
          <button className="btn primary sm" onClick={() => act(`/api/sales/orders/${id}/allocate`, {}, "Stock allocated (FEFO)")}>Allocate</button>}
        {st === "ALLOCATED" &&
          <button className="btn primary sm" onClick={() => act("/api/warehouse/pick", { sales_order_id: id }, "Pick tasks created")}>Create pick tasks</button>}
        {st === "PACKED" &&
          <button className="btn primary sm" onClick={() => act("/api/logistics/shipments", { sales_order_id: id, warehouse_id: "WH-MAIN" }, "Shipment planned")}>Plan shipment</button>}
        {st === "DELIVERED" &&
          <button className="btn primary sm" onClick={() => act(`/api/sales/orders/${id}/invoice`, {}, "Invoice issued")}>Invoice</button>}
        <div className="spacer" />
        <a className="small" style={{ color: "var(--blue)", fontWeight: 700, cursor: "pointer" }}
           href={`/workflows?entity=SALES_ORDER:${id}`}>Full workflow →</a>
      </div>
      {order.lines && (
        <table className="table mt">
          <thead><tr><th>SKU</th><th>Qty</th><th>Unit price</th><th>Amount</th></tr></thead>
          <tbody>
            {order.lines.map((l, i) => (
              <tr key={i}><td className="mono">{l.sku}</td><td>{l.quantity}</td>
                <td><Money value={l.unit_price} /></td><td><Money value={l.amount || l.quantity * l.unit_price} /></td></tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
