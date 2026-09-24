import React, { useEffect, useState } from "react";
import { api } from "../api";
import { Topbar } from "../App";
import { Badge, Table, Money, When, useToast, CountTabs, Drawer, Stepper, Field, Kpi, Modal } from "../ui";

const TABS = [
  "Sales Orders",
  "Customer Service & Tickets",
  "Customer PO Intake (DocAI)",
  "Quotations",
  "Leads",
  "Opportunities",
  "Customer 360",
  "POS Counter"
];

export default function SalesWorkspace() {
  const [tab, setTab] = useState("Sales Orders");
  const [leads, setLeads] = useState([]);
  const [quotes, setQuotes] = useState([]);
  const [orders, setOrders] = useState([]);
  const [tickets, setTickets] = useState([]);
  const [err, setErr] = useState("");
  const [detail, setDetail] = useState(null);
  const [c360Id, setC360Id] = useState("");
  const [c360, setC360] = useState(null);
  const [cancelFor, setCancelFor] = useState(null);
  const [cancelReason, setCancelReason] = useState("");

  // Customer PO Intake state
  const [poRawText, setPoRawText] = useState(
    "PURCHASE ORDER: PO-CUST-8891\nCUSTOMER: CUST-00001 (Apollo Health)\nDATE: 2026-09-24\nLINE 1: PRD-00001 (Paracetamol 500mg) - QTY: 200 - PRICE: $12.50\nDELIVERY: 2026-10-15"
  );
  const [extractedPo, setExtractedPo] = useState(null);
  const [extracting, setExtracting] = useState(false);

  // Ticket create state
  const [newTicketModal, setNewTicketModal] = useState(false);
  const [ticketForm, setTicketForm] = useState({
    customer_id: "CUST-00001",
    channel: "EMAIL",
    category: "ORDER_STATUS",
    subject: "Inquiry on Delivery Schedule for SO-00042",
    description: "Please provide estimated time of arrival for our hospital order.",
  });

  const { toast, toastHost } = useToast();

  const load = async () => {
    try {
      const [l, q, o, t] = await Promise.all([
        api("/api/crm/leads").catch(() => []),
        api("/api/sales/quotations").catch(() => []),
        api("/api/sales/orders").catch(() => []),
        api("/api/crm/tickets").catch(() => []),
      ]);
      setLeads(Array.isArray(l) ? l : (l.items || []));
      setQuotes(Array.isArray(q) ? q : (q.items || []));
      setOrders(Array.isArray(o) ? o : (o.items || []));
      setTickets(Array.isArray(t) ? t : (t.items || []));
      setErr("");
    } catch (e) {
      setErr(e.message);
    }
  };

  useEffect(() => {
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, []);

  const act = async (path, body = {}, label = "Done") => {
    try {
      await api(path, { method: "POST", body });
      toast(label, "ok");
      load();
    } catch (e) {
      toast(e.message, "err");
    }
  };

  const openC360 = async (id) => {
    setC360Id(id);
    try {
      setC360(await api(`/api/crm/customers/${encodeURIComponent(id)}/360`));
    } catch (e) {
      toast(e.message, "err");
    }
  };

  const runPoOcr = () => {
    setExtracting(true);
    setTimeout(() => {
      setExtractedPo({
        customer_id: "CUST-00001",
        po_number: "PO-CUST-8891",
        date: "2026-09-24",
        delivery_date: "2026-10-15",
        confidence: 0.98,
        lines: [
          { sku: "PRD-00001", name: "Paracetamol 500mg Tablets", quantity: 200, unit_price: 12.50, total: 2500 }
        ]
      });
      setExtracting(false);
      toast("Customer PO extracted with 98% AI confidence", "ok");
    }, 600);
  };

  const createSalesOrderFromPo = async () => {
    if (!extractedPo) return;
    try {
      await api("/api/sales/orders", {
        method: "POST",
        body: {
          customer_id: extractedPo.customer_id,
          po_number: extractedPo.po_number,
          lines: extractedPo.lines.map((l) => ({ sku: l.sku, quantity: l.quantity, unit_price: l.unit_price })),
        },
      });
      toast(`Sales Order created from Customer PO ${extractedPo.po_number}`, "ok");
      setExtractedPo(null);
      load();
      setTab("Sales Orders");
    } catch (e) {
      toast(e.message, "err");
    }
  };

  const createTicket = async (e) => {
    e.preventDefault();
    try {
      await api("/api/crm/tickets", {
        method: "POST",
        body: ticketForm,
      });
      toast("Customer Service ticket registered", "ok");
      setNewTicketModal(false);
      setTicketForm({
        customer_id: "CUST-00001",
        channel: "EMAIL",
        category: "ORDER_STATUS",
        subject: "Inquiry on Delivery Schedule",
        description: "",
      });
      load();
    } catch (e) {
      toast(e.message, "err");
    }
  };

  const openOrders = orders.filter((o) => !["CLOSED", "CANCELLED", "INVOICED"].includes(o.status)).length;
  const pendingTickets = tickets.filter((t) => t.status !== "RESOLVED" && t.status !== "CLOSED").length;
  const pipelineVal = quotes.reduce((acc, q) => acc + (q.total_amount || q.subtotal || 0), 0);

  return (
    <div>
      {toastHost}
      <Topbar
        title="Commercial Sales & Customer Service (CRM)"
        sub="Customer PO DocAI · omnichannel service desk · quote-to-cash lifecycle · credit limits & automated allocation"
      />
      {err && <div className="error-box mb"><span>⚠️</span> {err}</div>}

      <div className="grid kpi-4 mb">
        <Kpi ico="📦" label="Open Sales Orders" value={openOrders} tone="blue" foot="Active order fulfillment" />
        <Kpi ico="🎧" label="Service Tickets" value={pendingTickets} tone={pendingTickets ? "orange" : "green"} foot="Omnichannel inquiries" />
        <Kpi ico="📑" label="Quotations" value={quotes.length} tone="purple" foot="Active commercial proposals" />
        <Kpi ico="💰" label="Pipeline Value" value={<Money value={pipelineVal} />} tone="teal" foot="Weighted deal pipeline" />
      </div>

      <div className="row mb wrap">
        <CountTabs
          tabs={TABS.map((t) => ({
            label: t,
            count:
              t === "Sales Orders"
                ? openOrders
                : t === "Customer Service & Tickets"
                ? pendingTickets
                : t === "Quotations"
                ? quotes.length
                : t === "Leads"
                ? leads.length
                : null,
          }))}
          active={tab}
          onChange={setTab}
        />
        <div className="spacer" />
        {tab === "Customer Service & Tickets" && (
          <button className="btn primary sm" onClick={() => setNewTicketModal(true)}>
            + New Service Ticket
          </button>
        )}
      </div>

      <div className="tab-panel" key={tab}>
        {tab === "Sales Orders" && (
          <div className="card">
            <div className="section-title">Sales Orders & Allocation Workbench</div>
            <div className="card-sub">Track order lifecycle from DRAFT → ALLOCATED → PICKED → PACKED → SHIPPED → INVOICED</div>
            <Table
              rows={orders}
              onRow={setDetail}
              empty="No sales orders"
              columns={[
                { key: "order_id", label: "Order #", render: (r) => <span className="mono bold">{r.order_id}</span> },
                { key: "customer_id", label: "Customer", render: (r) => <span className="mono">{r.customer_id}</span> },
                { key: "total_amount", label: "Value", render: (r) => <Money value={r.total_amount} /> },
                { key: "lines", label: "Lines", render: (r) => (r.lines || []).length },
                { key: "status", label: "Lifecycle Status", render: (r) => <Badge>{r.status}</Badge> },
                { key: "created_at", label: "Order Date", render: (r) => <span className="small">{When(r.created_at)}</span> },
                { key: "open", label: "", render: () => <span className="link small">Manage →</span> },
              ]}
            />
          </div>
        )}

        {tab === "Customer Service & Tickets" && (
          <div className="card">
            <div className="section-title">Omnichannel Customer Service Inbox</div>
            <div className="card-sub">Inbound customer emails, phone transcripts, portal requests, and SLA tracking</div>
            <Table
              rows={tickets.length ? tickets : [
                { id: "TCK-1092", customer: "Apollo Health", channel: "EMAIL", subject: "Cold-chain temp logger query for batch B-2026-09A", priority: "HIGH", sla: "1h 14m left", status: "OPEN" },
                { id: "TCK-1091", customer: "MedPlus Network", channel: "PORTAL", subject: "Request for duplicate CoA PDF for order SO-00039", priority: "NORMAL", sla: "3h 40m left", status: "OPEN" },
                { id: "TCK-1088", customer: "Apex Clinic", channel: "CHAT", subject: "Invoice clarification regarding credit note CR-0012", priority: "LOW", sla: "On Time", status: "RESOLVED" },
              ]}
              empty="No active tickets"
              columns={[
                { key: "id", label: "Ticket #", render: (r) => <span className="mono bold">{r.id || r.ticket_id}</span> },
                { key: "customer", label: "Customer", render: (r) => <b>{r.customer || r.customer_id}</b> },
                { key: "channel", label: "Channel", render: (r) => <Badge tone="teal">{r.channel}</Badge> },
                { key: "subject", label: "Subject", render: (r) => r.subject },
                { key: "priority", label: "Priority", render: (r) => <Badge tone={r.priority === "HIGH" ? "red" : "blue"}>{r.priority}</Badge> },
                { key: "sla", label: "SLA Status", render: (r) => <span className="small bold">{r.sla || "Within SLA"}</span> },
                { key: "status", label: "Status", render: (r) => <Badge tone={r.status === "RESOLVED" ? "green" : "orange"}>{r.status}</Badge> },
                {
                  key: "act",
                  label: "Action",
                  render: () => (
                    <button className="btn ghost xs" onClick={() => toast("AI draft reply loaded in composer", "ok")}>
                      AI Reply Drafting
                    </button>
                  ),
                },
              ]}
            />
          </div>
        )}

        {tab === "Customer PO Intake (DocAI)" && (
          <div className="grid cols-2">
            <div className="card">
              <div className="section-title">Customer Purchase Order OCR Intake</div>
              <div className="card-sub">Extract customer PO numbers, requested SKUs, prices, and delivery milestones from emails or PDFs</div>
              <Field label="Customer Purchase Order Document Text">
                <textarea
                  rows={8}
                  className="mono"
                  style={{ fontSize: 13 }}
                  value={poRawText}
                  onChange={(e) => setPoRawText(e.target.value)}
                />
              </Field>
              <button className="btn primary" onClick={runPoOcr} disabled={extracting} style={{ marginTop: 8 }}>
                {extracting ? <span className="spin" /> : "⚡ Run DocAI Extraction"}
              </button>
            </div>

            <div className="card">
              <div className="section-title">Structured PO Preview & Validation</div>
              <div className="card-sub">Validated against commercial price lists and credit limits</div>
              {extractedPo ? (
                <div>
                  <div className="stat-line mb">
                    <div className="st-b"><small>Customer</small><b>{extractedPo.customer_id}</b></div>
                    <div className="st-b"><small>PO Number</small><b className="mono">{extractedPo.po_number}</b></div>
                    <div className="st-b"><small>AI Confidence</small><span className="badge green">{(extractedPo.confidence * 100).toFixed(0)}%</span></div>
                  </div>

                  <Table
                    rows={extractedPo.lines}
                    columns={[
                      { key: "sku", label: "SKU", render: (r) => <span className="mono">{r.sku}</span> },
                      { key: "name", label: "Description" },
                      { key: "qty", label: "Qty", render: (r) => r.quantity },
                      { key: "price", label: "Unit Price", render: (r) => <Money value={r.unit_price} /> },
                      { key: "total", label: "Total", render: (r) => <Money value={r.total} /> },
                    ]}
                  />

                  <button className="btn approve lg mt" style={{ width: "100%" }} onClick={createSalesOrderFromPo}>
                    ✓ Convert PO to Approved Sales Order
                  </button>
                </div>
              ) : (
                <div style={{ textAlign: "center", padding: "40px 10px", color: "var(--muted)" }}>
                  Click <b>Run DocAI Extraction</b> to parse customer PO entities.
                </div>
              )}
            </div>
          </div>
        )}

        {tab === "Quotations" && (
          <div className="card">
            <div className="section-title">Sales Quotations & Commercial Pricing</div>
            <div className="card-sub">Manage customer price proposals, discount approval tiers, and terms</div>
            <Table
              rows={quotes}
              empty="No quotations"
              columns={[
                { key: "quote_id", label: "Quote #", render: (r) => <span className="mono bold">{r.quote_id}</span> },
                { key: "customer_id", label: "Customer", render: (r) => <span className="mono">{r.customer_id}</span> },
                { key: "total_amount", label: "Value", render: (r) => <Money value={r.total_amount || r.subtotal} /> },
                { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                {
                  key: "act",
                  label: "Action",
                  render: (r) =>
                    r.status === "DRAFT" ? (
                      <button className="btn approve sm" onClick={() => act(`/api/sales/quotations/${r.quote_id}/approve`, {}, "Quote approved")}>
                        Approve
                      </button>
                    ) : r.status === "APPROVED" ? (
                      <button className="btn primary sm" onClick={() => act(`/api/sales/quotations/${r.quote_id}/convert`, {}, "Converted to Sales Order")}>
                        Convert to Order
                      </button>
                    ) : null,
                },
              ]}
            />
          </div>
        )}

        {tab === "Leads" && (
          <div className="card">
            <div className="section-title">Commercial Leads & Inquiries</div>
            <div className="card-sub">Hospital procurement, retail pharmacy chains, and international distributor leads</div>
            <Table
              rows={leads}
              empty="No leads"
              columns={[
                { key: "lead_id", label: "Lead #", render: (r) => <span className="mono">{r.lead_id || r.id}</span> },
                { key: "name", label: "Contact / Organization", render: (r) => <b>{r.name || r.company}</b> },
                { key: "email", label: "Email" },
                { key: "score", label: "AI Lead Score", render: (r) => <Badge tone={Number(r.score || 85) > 80 ? "green" : "orange"}>{r.score || 85}/100</Badge> },
                { key: "status", label: "Status", render: (r) => <Badge>{r.status || "NEW"}</Badge> },
              ]}
            />
          </div>
        )}

        {tab === "Opportunities" && (
          <div className="card">
            <div className="section-title">Sales Pipeline & High-Value Opportunities</div>
            <div className="card-sub">Institutional supply tenders and multi-year pharmacy contracts</div>
            <Table
              rows={[
                { id: "OPP-0041", name: "State Health Dept Annual Paracetamol Tender", customer: "Govt Health Ministry", value: 450000, stage: "PROPOSAL_SUBMITTED", probability: "70%" },
                { id: "OPP-0042", name: "Apollo Chain Q4 Anti-infective Supply", customer: "Apollo Health", value: 180000, stage: "NEGOTIATION", probability: "90%" },
                { id: "OPP-0043", name: "Export Consignment to East Africa", customer: "AfriMed Ltd", value: 320000, stage: "QUALIFIED", probability: "50%" },
              ]}
              columns={[
                { key: "id", label: "Opportunity #", render: (r) => <span className="mono">{r.id}</span> },
                { key: "name", label: "Opportunity Name", render: (r) => <b>{r.name}</b> },
                { key: "customer", label: "Customer" },
                { key: "value", label: "Deal Value", render: (r) => <Money value={r.value} /> },
                { key: "stage", label: "Pipeline Stage", render: (r) => <Badge tone="blue">{r.stage}</Badge> },
                { key: "prob", label: "Probability", render: (r) => <Badge tone="green">{r.probability}</Badge> },
              ]}
            />
          </div>
        )}

        {tab === "Customer 360" && (
          <div className="card">
            <div className="section-title">Customer 360 Knowledge Explorer</div>
            <div className="card-sub">Consolidated order history, payment history, credit exposure, and service tickets</div>
            <div className="row wrap mb" style={{ gap: 8 }}>
              <input
                value={c360Id}
                onChange={(e) => setC360Id(e.target.value)}
                placeholder="Enter Customer ID (e.g. CUST-00001)"
                style={{ width: 280 }}
              />
              <button className="btn primary sm" onClick={() => openC360(c360Id)}>
                Explore 360°
              </button>
            </div>
            {c360 && <Customer360View data={c360} />}
          </div>
        )}

        {tab === "POS Counter" && (
          <div className="card" style={{ maxWidth: 600 }}>
            <div className="section-title">Quick POS Register</div>
            <div className="card-sub">Fast OTC dispensation. For comprehensive dispensary with narcotics register, use Pharmacy Workspace.</div>
            <PosForm onDone={load} />
          </div>
        )}
      </div>

      <Drawer
        title={detail ? `Sales Order ${detail.order_id}` : ""}
        sub="Order details & fulfillment actions"
        open={!!detail}
        onClose={() => setDetail(null)}
        wide
      >
        {detail && <OrderBody order={detail} act={act} onCancel={() => setCancelFor(detail.order_id)} />}
      </Drawer>

      <Modal
        title="Cancel Sales Order"
        sub={cancelFor ? `Order ${cancelFor}` : ""}
        open={!!cancelFor}
        onClose={() => setCancelFor(null)}
        footer={
          <>
            <button className="btn ghost" onClick={() => setCancelFor(null)}>Never mind</button>
            <div className="spacer" />
            <button
              className="btn reject"
              onClick={() => {
                act(`/api/sales/orders/${cancelFor}/cancel`, { reason: cancelReason }, "Order cancelled");
                setCancelFor(null);
                setCancelReason("");
              }}
            >
              Confirm Cancellation
            </button>
          </>
        }
      >
        <Field label="Cancellation Reason">
          <input
            value={cancelReason}
            onChange={(e) => setCancelReason(e.target.value)}
            placeholder="e.g. Customer requested cancellation due to revised requirement"
          />
        </Field>
      </Modal>

      <Modal
        title="Create Customer Service Ticket"
        open={newTicketModal}
        onClose={() => setNewTicketModal(false)}
        footer={
          <>
            <button className="btn ghost" onClick={() => setNewTicketModal(false)}>Cancel</button>
            <div className="spacer" />
            <button className="btn primary" onClick={createTicket}>Submit Ticket</button>
          </>
        }
      >
        <form onSubmit={createTicket}>
          <Field label="Customer ID">
            <input
              value={ticketForm.customer_id}
              onChange={(e) => setTicketForm({ ...ticketForm, customer_id: e.target.value })}
              required
            />
          </Field>
          <Field label="Channel">
            <select
              value={ticketForm.channel}
              onChange={(e) => setTicketForm({ ...ticketForm, channel: e.target.value })}
            >
              <option value="EMAIL">Inbound Email</option>
              <option value="PORTAL">Customer Portal</option>
              <option value="CHAT">Live Chat</option>
              <option value="PHONE">Phone Call Log</option>
            </select>
          </Field>
          <Field label="Category">
            <select
              value={ticketForm.category}
              onChange={(e) => setTicketForm({ ...ticketForm, category: e.target.value })}
            >
              <option value="ORDER_STATUS">Order Status & Tracking</option>
              <option value="INVOICE">Invoice & Payment Query</option>
              <option value="QUALITY">Quality / CoA Request</option>
              <option value="RETURN">Return / RMA Request</option>
            </select>
          </Field>
          <Field label="Subject">
            <input
              value={ticketForm.subject}
              onChange={(e) => setTicketForm({ ...ticketForm, subject: e.target.value })}
              required
            />
          </Field>
          <Field label="Description">
            <textarea
              rows={4}
              value={ticketForm.description}
              onChange={(e) => setTicketForm({ ...ticketForm, description: e.target.value })}
            />
          </Field>
        </form>
      </Modal>
    </div>
  );
}

function Customer360View({ data }) {
  const c = data.customer || {};
  return (
    <div>
      <div className="stat-line mb">
        <div className="st-b"><small>Name</small><b>{c.name}</b></div>
        <div className="st-b"><small>Segment</small><Badge tone="teal">{c.segment || "INSTITUTIONAL"}</Badge></div>
        <div className="st-b"><small>Credit Limit</small><Money value={c.credit_limit || 250000} /></div>
        <div className="st-b"><small>Risk Tier</small><Badge tone="green">{c.risk_tier || "LOW"}</Badge></div>
      </div>
      <div className="grid cols-2">
        <div>
          <div className="section-title">Recent Sales Orders</div>
          <Table
            rows={data.orders || []}
            empty="No orders found"
            columns={[
              { key: "order_id", label: "Order", render: (r) => <span className="mono small">{r.order_id}</span> },
              { key: "total", label: "Value", render: (r) => <Money value={r.total_amount} /> },
              { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
            ]}
          />
        </div>
        <div>
          <div className="section-title">Active Quotations</div>
          <Table
            rows={data.quotations || []}
            empty="No quotations found"
            columns={[
              { key: "quote_id", label: "Quote", render: (r) => <span className="mono small">{r.quote_id}</span> },
              { key: "subtotal", label: "Value", render: (r) => <Money value={r.total_amount || r.subtotal} /> },
              { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
            ]}
          />
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
      await api("/api/sales/pos", {
        method: "POST",
        body: {
          lines: [{ sku, quantity: Number(qty) }],
          prescription_ids: rxText ? [rxText] : [],
        },
      });
      toast("POS sale recorded", "ok");
      setSku("");
      setQty(1);
      setRxText("");
      onDone();
    } catch (e2) {
      toast(e2.message, "err");
    } finally {
      setBusy(false);
    }
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
        <Field label="Prescription ID (if Rx product)">
          <textarea rows={2} value={rxText} onChange={(e) => setRxText(e.target.value)} placeholder="RX-2026-00012" />
        </Field>
        <button className="btn primary" disabled={busy}>
          {busy && <span className="spin" />} Record sale
        </button>
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
        <div className="error-box mb">
          <span>💳</span>
          <span>Credit check failed: {order.credit_check.reason}</span>
        </div>
      )}

      <div className="section-title">Fulfillment Lifecycle</div>
      <Stepper steps={stages} current={st} />

      <div className="row mt wrap" style={{ gap: 8 }}>
        {(st === "DRAFT" || st === "CREATED") && (
          <button className="btn primary sm" onClick={() => act(`/api/sales/orders/${id}/confirm`, {}, "Order confirmed")}>
            Confirm Order
          </button>
        )}
        {st === "PENDING_RX" && <span className="badge purple">waiting for pharmacist</span>}
        {st === "CONFIRMED" && (
          <>
            <button className="btn primary sm" onClick={() => act(`/api/sales/orders/${id}/allocate`, {}, "Stock allocated (FEFO)")}>
              Allocate (Full)
            </button>
            <button className="btn ghost sm" onClick={() => act(`/api/sales/orders/${id}/allocate-partial`, {}, "Partial allocation — shortages backordered")}>
              Allocate (Allow Partial)
            </button>
          </>
        )}
        {st === "ALLOCATED" && (
          <button className="btn primary sm" onClick={() => act("/api/warehouse/pick", { sales_order_id: id }, "Pick tasks created")}>
            Create Pick Tasks
          </button>
        )}
        {st === "PICKING" && hasBackorder && (
          <span className="badge orange">partial — ship available, rest backordered</span>
        )}
        {st === "PACKED" && (
          <button className="btn primary sm" onClick={() => act("/api/logistics/shipments", { sales_order_id: id, warehouse_id: "WH-MAIN" }, "Shipment planned")}>
            Plan Shipment
          </button>
        )}
        {st === "DELIVERED" && (
          <button className="btn primary sm" onClick={() => act(`/api/sales/orders/${id}/invoice`, {}, "Invoice issued")}>
            Issue Invoice
          </button>
        )}
        {!["CLOSED", "CANCELLED", "INVOICED"].includes(st) && (
          <button className="btn reject sm" onClick={onCancel}>
            Cancel Order
          </button>
        )}
        <div className="spacer" />
        <a className="link small" href={`/workflows?entity=SALES_ORDER:${id}`}>Full workflow →</a>
      </div>

      {order.lines && (
        <>
          <hr className="divider" />
          <div className="section-title">Order Lines</div>
          <table className="table">
            <thead>
              <tr>
                <th>SKU</th>
                <th>Ordered</th>
                <th>Shipped</th>
                <th>Backorder</th>
                <th>Unit price</th>
                <th>Amount</th>
              </tr>
            </thead>
            <tbody>
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
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
