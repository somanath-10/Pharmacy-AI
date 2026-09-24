import React, { useEffect, useState } from "react";
import { api } from "../api";
import { Topbar } from "../App";
import { Badge, Table, Money, When, useToast, CountTabs, Drawer, Stepper, Field, Kpi, Modal } from "../ui";

const TABS = [
  "Purchase Orders",
  "Purchase Requisitions",
  "Buyer Work Queue",
  "Vendors & AVL",
  "Supplier Scorecards",
  "Sourcing & RFQ",
  "Contracts & BPAs",
  "Spend Analytics"
];

export default function VendorsWorkspace() {
  const [tab, setTab] = useState("Purchase Orders");
  const [vendors, setVendors] = useState([]);
  const [events, setEvents] = useState([]);
  const [contracts, setContracts] = useState([]);
  const [prs, setPrs] = useState([]);
  const [pos, setPos] = useState([]);
  const [detail, setDetail] = useState(null);
  const [newPrModal, setNewPrModal] = useState(false);
  const [prForm, setPrForm] = useState({ title: "", sku: "RAW-001", quantity: 50, estimated_amount: 15000 });
  const [err, setErr] = useState("");
  const { toast, toastHost } = useToast();

  const load = async () => {
    try {
      const [v, ev, ct, pr, po] = await Promise.all([
        api("/api/vendors").catch(() => []),
        api("/api/sourcing/events").catch(() => []),
        api("/api/sourcing/contracts").catch(() => []),
        api("/api/procurement/prs").catch(() => []),
        api("/api/procurement/pos").catch(() => []),
      ]);
      setVendors(Array.isArray(v) ? v : (v.items || []));
      setEvents(Array.isArray(ev) ? ev : (ev.items || []));
      setContracts(Array.isArray(ct) ? ct : (ct.items || []));
      setPrs(Array.isArray(pr) ? pr : (pr.items || []));
      setPos(Array.isArray(po) ? po : (po.items || []));
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

  const createPr = async (e) => {
    e.preventDefault();
    try {
      await api("/api/procurement/prs", {
        method: "POST",
        body: {
          title: prForm.title,
          lines: [{ sku: prForm.sku, quantity: Number(prForm.quantity) }],
          estimated_amount: Number(prForm.estimated_amount),
        },
      });
      toast("Purchase Requisition created (DRAFT)", "ok");
      setNewPrModal(false);
      setPrForm({ title: "", sku: "RAW-001", quantity: 50, estimated_amount: 15000 });
      load();
    } catch (err) {
      toast(err.message, "err");
    }
  };

  const poStages = ["DRAFT", "PENDING_APPROVAL", "APPROVED", "SENT", "ACKNOWLEDGED", "RECEIVED", "CLOSED"];
  const pendingPrs = prs.filter((p) => p.status === "DRAFT" || p.status === "SUBMITTED").length;
  const approvedPrs = prs.filter((p) => p.status === "APPROVED").length;

  return (
    <div>
      {toastHost}
      <Topbar
        title="Procurement & Sourcing (P2P)"
        sub="Approved Vendor List (AVL) → RFQ & reverse auctions → contracts → PR auto-consolidation → PO tracking"
      />
      {err && <div className="error-box mb"><span>⚠️</span> {err}</div>}

      <div className="grid kpi-4 mb">
        <Kpi ico="🛒" label="Purchase Orders" value={pos.length} tone="blue" foot="Active procurement contracts" />
        <Kpi ico="📥" label="Pending PRs" value={pendingPrs} tone={pendingPrs ? "orange" : "green"} foot="Requisitions awaiting approval" />
        <Kpi ico="🤝" label="Active Contracts" value={contracts.length} tone="purple" foot="Blanket agreements & price locks" />
        <Kpi ico="🏭" label="Qualified Vendors" value={vendors.filter((v) => v.status === "APPROVED").length} tone="teal" foot="Audited GMP suppliers" />
      </div>

      <div className="row mb wrap">
        <CountTabs
          tabs={TABS.map((t) => ({
            label: t,
            count:
              t === "Vendors & AVL"
                ? vendors.length
                : t === "Sourcing & RFQ"
                ? events.length
                : t === "Contracts & BPAs"
                ? contracts.length
                : t === "Purchase Requisitions"
                ? prs.length
                : t === "Buyer Work Queue"
                ? approvedPrs
                : pos.length,
          }))}
          active={tab}
          onChange={setTab}
        />
        <div className="spacer" />
        <button className="btn primary sm" onClick={() => setNewPrModal(true)}>
          + New Requisition (PR)
        </button>
        <button
          className="btn ghost"
          onClick={() => act("/api/agents/run/procurement-agent", { goal: "replenish_shortages" }, "Procurement agent run started")}
        >
          🤖 Run Procurement Agent
        </button>
      </div>

      <div className="tab-panel" key={tab}>
        {tab === "Purchase Orders" && (
          <div className="card">
            <div className="section-title">Purchase Orders (P2P)</div>
            <div className="card-sub">PO → approval → send → ack → ASN → GRN → QC → 4-way match → payment</div>
            <Table
              rows={pos}
              onRow={setDetail}
              empty="No POs"
              columns={[
                { key: "po_id", label: "PO #", render: (r) => <span className="mono bold">{r.po_id}</span> },
                { key: "vendor_id", label: "Vendor", render: (r) => <span className="mono">{r.vendor_id}</span> },
                { key: "total_amount", label: "Value", render: (r) => <Money v={r.total_amount} /> },
                { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                {
                  key: "approval",
                  label: "Approval Type",
                  render: (r) =>
                    r.approval ? (
                      <Badge tone={r.approval.type === "HUMAN" ? "orange" : "green"}>
                        {r.approval.type === "HUMAN" ? "HUMAN_QUEUE" : "AUTO"}
                      </Badge>
                    ) : (
                      <span className="muted">—</span>
                    ),
                },
                { key: "open", label: "", render: () => <span className="link small">Manage →</span> },
              ]}
            />
          </div>
        )}

        {tab === "Purchase Requisitions" && (
          <div className="card">
            <div className="section-title">Purchase Requisitions (PR)</div>
            <div className="card-sub">Departmental material requests before buyer PO conversion</div>
            <Table
              rows={prs}
              empty="No purchase requisitions"
              columns={[
                { key: "pr_id", label: "PR #", render: (r) => <span className="mono bold">{r.pr_id}</span> },
                { key: "title", label: "Description", render: (r) => <b>{r.title}</b> },
                { key: "estimated_amount", label: "Estimated Value", render: (r) => <Money v={r.estimated_amount} /> },
                { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                {
                  key: "act",
                  label: "Action",
                  render: (r) =>
                    r.status === "DRAFT" || r.status === "SUBMITTED" ? (
                      <button className="btn approve sm" onClick={() => act(`/api/procurement/prs/${r.pr_id}/approve`, {}, "PR Approved")}>
                        Approve PR
                      </button>
                    ) : null,
                },
              ]}
            />
          </div>
        )}

        {tab === "Buyer Work Queue" && (
          <div className="card">
            <div className="section-title">Buyer Conversion Workbench</div>
            <div className="card-sub">Approved purchase requisitions ready to be bundled into commercial POs</div>
            <Table
              rows={prs.filter((p) => p.status === "APPROVED")}
              empty="No approved PRs awaiting buyer conversion"
              columns={[
                { key: "pr_id", label: "PR #", render: (r) => <span className="mono bold">{r.pr_id}</span> },
                { key: "title", label: "Requisition Item", render: (r) => <b>{r.title}</b> },
                { key: "lines", label: "Lines", render: (r) => (r.lines || []).length },
                { key: "val", label: "Value", render: (r) => <Money v={r.estimated_amount} /> },
                {
                  key: "act",
                  label: "Conversion",
                  render: (r) => (
                    <button
                      className="btn primary sm"
                      onClick={() =>
                        act("/api/procurement/pos", {
                          pr_id: r.pr_id,
                          vendor_id: "VEN-00001",
                          lines: r.lines || [{ sku: "RAW-001", quantity: 100 }],
                        }, "Purchase Order Created from PR")
                      }
                    >
                      Convert to PO →
                    </button>
                  ),
                },
              ]}
            />
          </div>
        )}

        {tab === "Vendors & AVL" && (
          <div className="card">
            <div className="section-title">Approved Vendor List (AVL) & Qualification</div>
            <div className="card-sub">Vendor qualification · GMP certificates · ISO · risk scoring · drug licenses</div>
            <Table
              rows={vendors}
              empty="No vendors registered"
              columns={[
                { key: "vendor_id", label: "Vendor ID", render: (r) => <span className="mono bold">{r.vendor_id}</span> },
                { key: "name", label: "Supplier Organization", render: (r) => <b>{r.name}</b> },
                { key: "category", label: "Category" },
                { key: "risk_level", label: "Risk Tier", render: (r) => <Badge tone={r.risk_level === "LOW" ? "green" : r.risk_level === "HIGH" ? "red" : "orange"}>{r.risk_level || "MEDIUM"}</Badge> },
                { key: "status", label: "AVL Status", render: (r) => <Badge tone={r.status === "APPROVED" ? "green" : "red"}>{r.status}</Badge> },
              ]}
            />
          </div>
        )}

        {tab === "Supplier Scorecards" && (
          <div className="grid cols-2">
            <div className="card">
              <div className="section-title">Supplier Performance Scorecards (OTIF & Quality PPM)</div>
              <div className="card-sub">Quarterly rating based on On-Time-In-Full (OTIF), CoA accuracy, and OOS rate</div>
              <Table
                rows={[
                  { vendor: "Aurobindo Fine Chemicals", otif: "98.4%", ppm: "12 PPM", rating: "A+", badge: "PREFERRED" },
                  { vendor: "Cipla Bulk Actives", otif: "96.1%", ppm: "45 PPM", rating: "A", badge: "APPROVED" },
                  { vendor: "Hetero Solvents Ltd", otif: "88.2%", ppm: "210 PPM", rating: "B", badge: "CONDITIONAL" },
                  { vendor: "Novartis Packaging Corp", otif: "99.1%", ppm: "0 PPM", rating: "A+", badge: "PREFERRED" },
                ]}
                columns={[
                  { key: "vendor", label: "Supplier Name", render: (r) => <b>{r.vendor}</b> },
                  { key: "otif", label: "OTIF Rate", render: (r) => <span className="mono bold">{r.otif}</span> },
                  { key: "ppm", label: "Defect PPM", render: (r) => <span className="mono">{r.ppm}</span> },
                  { key: "rating", label: "Grade", render: (r) => <Badge tone="green">{r.rating}</Badge> },
                  { key: "badge", label: "Status", render: (r) => <Badge tone={r.badge === "PREFERRED" ? "teal" : r.badge === "APPROVED" ? "blue" : "orange"}>{r.badge}</Badge> },
                ]}
              />
            </div>
            <div className="card">
              <div className="section-title">Supplier Risk Matrix</div>
              <div className="card-sub">Single-source dependency, financial stability, and geopolitical supply risk</div>
              <div className="grid" style={{ gap: 10 }}>
                <div className="card tinted">
                  <div className="row">
                    <b>Critical API Single Source Exposure</b>
                    <span className="badge orange">2 Items</span>
                  </div>
                  <div className="small muted">Paracetamol IP & Amoxicillin Trihydrate active dual-sourcing validation underway</div>
                </div>
                <div className="card tinted">
                  <div className="row">
                    <b>Upcoming GMP Certificate Expirations</b>
                    <span className="badge green">0 Expiring &lt; 60d</span>
                  </div>
                  <div className="small muted">All primary suppliers audited and certified through 2027</div>
                </div>
              </div>
            </div>
          </div>
        )}

        {tab === "Sourcing & RFQ" && (
          <div className="card">
            <div className="section-title">Sourcing Projects & e-Auctions</div>
            <div className="card-sub">RFI → RFP → RFQ intake → multi-round reverse auctions and Best Commercial Evaluation</div>
            <Table
              rows={events}
              empty="No sourcing events"
              columns={[
                { key: "event_id", label: "Event #", render: (r) => <span className="mono">{r.event_id}</span> },
                { key: "title", label: "Sourcing Event Title", render: (r) => <b>{r.title}</b> },
                { key: "event_type", label: "Type", render: (r) => <Badge tone="teal">{r.event_type || "RFQ"}</Badge> },
                { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
              ]}
            />
          </div>
        )}

        {tab === "Contracts & BPAs" && (
          <div className="card">
            <div className="section-title">Blanket Purchase Agreements (BPA) & Contracts</div>
            <div className="card-sub">Long-term volume commitments with locked tiered pricing</div>
            <Table
              rows={contracts}
              empty="No active contracts"
              columns={[
                { key: "contract_id", label: "Contract #", render: (r) => <span className="mono bold">{r.contract_id}</span> },
                { key: "vendor_id", label: "Supplier", render: (r) => <span className="mono">{r.vendor_id}</span> },
                { key: "title", label: "Agreement Title", render: (r) => <b>{r.title}</b> },
                { key: "status", label: "Status", render: (r) => <Badge tone="green">{r.status || "ACTIVE"}</Badge> },
              ]}
            />
          </div>
        )}

        {tab === "Spend Analytics" && (
          <div className="grid cols-2">
            <div className="card">
              <div className="section-title">Procurement Spend by Category</div>
              <div className="card-sub">Direct Raw Materials, Excipients, Primary Packaging, and Indirect Consumables</div>
              <div className="grid" style={{ gap: 12 }}>
                {[
                  { cat: "Active Pharmaceutical Ingredients (API)", val: "$2,840,000", pct: "52%", tone: "var(--primary)" },
                  { cat: "Excipients & Binders", val: "$920,000", pct: "17%", tone: "var(--teal)" },
                  { cat: "Primary Packaging (Blister Foil, PVC)", val: "$760,000", pct: "14%", tone: "var(--blue)" },
                  { cat: "Secondary Packaging (Cartons, Leaflets)", val: "$480,000", pct: "9%", tone: "var(--orange)" },
                  { cat: "MRO & Laboratory Solvents", val: "$440,000", pct: "8%", tone: "var(--purple)" },
                ].map((s) => (
                  <div key={s.cat}>
                    <div className="row small">
                      <b>{s.cat}</b>
                      <div className="spacer" />
                      <span>{s.val} ({s.pct})</span>
                    </div>
                    <div style={{ height: 6, background: "rgba(0,0,0,0.06)", borderRadius: 3, marginTop: 4, overflow: "hidden" }}>
                      <div style={{ height: "100%", width: s.pct, background: s.tone }} />
                    </div>
                  </div>
                ))}
              </div>
            </div>
            <div className="card">
              <div className="section-title">Tail Spend & Contract Leakage</div>
              <div className="card-sub">Compliance rate of purchases against negotiated master contracts</div>
              <div className="grid cols-2 mb" style={{ gap: 10 }}>
                <div className="card tinted">
                  <small className="muted">Contract Compliance</small>
                  <div style={{ fontSize: 24, fontWeight: "bold", color: "var(--green)" }}>94.2%</div>
                  <small className="muted">Target: &gt; 90%</small>
                </div>
                <div className="card tinted">
                  <small className="muted">Tail Spend Proportion</small>
                  <div style={{ fontSize: 24, fontWeight: "bold", color: "var(--teal)" }}>5.8%</div>
                  <small className="muted">Under strict control</small>
                </div>
              </div>
              <div className="small muted">
                Automated purchase orders generated from MRP proposals enforce Blanket Purchase Agreement (BPA) price locks and volume tier rebate thresholds.
              </div>
            </div>
          </div>
        )}
      </div>

      <Drawer title={`PO ${detail?.po_id || ""}`} sub="Procure-to-pay lifecycle" open={!!detail} onClose={() => setDetail(null)}>
        {detail && <PoBody po={detail} act={act} stages={poStages} />}
      </Drawer>

      <Modal title="Create Purchase Requisition (PR)" open={newPrModal} onClose={() => setNewPrModal(false)}>
        <form onSubmit={createPr}>
          <Field label="Requisition Title">
            <input
              required
              placeholder="e.g. Q4 Paracetamol API Restock"
              value={prForm.title}
              onChange={(e) => setPrForm({ ...prForm, title: e.target.value })}
            />
          </Field>
          <div className="row wrap" style={{ gap: 12 }}>
            <Field label="Material SKU">
              <input required value={prForm.sku} onChange={(e) => setPrForm({ ...prForm, sku: e.target.value })} />
            </Field>
            <Field label="Quantity Required">
              <input
                type="number"
                required
                min="1"
                value={prForm.quantity}
                onChange={(e) => setPrForm({ ...prForm, quantity: e.target.value })}
              />
            </Field>
          </div>
          <Field label="Estimated Total Value ($)">
            <input
              type="number"
              required
              min="1"
              value={prForm.estimated_amount}
              onChange={(e) => setPrForm({ ...prForm, estimated_amount: e.target.value })}
            />
          </Field>
          <div className="row mt" style={{ gap: 8 }}>
            <button type="button" className="btn ghost" onClick={() => setNewPrModal(false)}>Cancel</button>
            <div className="spacer" />
            <button type="submit" className="btn primary">Submit Requisition</button>
          </div>
        </form>
      </Modal>
    </div>
  );
}

function PoBody({ po, act, stages }) {
  const id = po.po_id;
  const st = po.status;
  return (
    <div>
      <div className="row mb">
        <Badge>{st}</Badge>
        <span className="small muted">{po.vendor_id}</span>
        <div className="spacer" />
        <Money v={po.total_amount} />
      </div>
      <div className="section-title">Lifecycle</div>
      <Stepper steps={stages} current={st} />
      <div className="row mt wrap" style={{ gap: 8 }}>
        {st === "DRAFT" && (
          <button className="btn primary sm" onClick={() => act(`/api/procurement/pos/${id}/submit`, {}, "PO submitted for approval")}>
            Submit for Approval
          </button>
        )}
        {st === "APPROVED" && (
          <button className="btn primary sm" onClick={() => act(`/api/procurement/pos/${id}/send`, {}, "PO sent to vendor")}>
            Send to Vendor
          </button>
        )}
        {st === "SENT" && (
          <button className="btn primary sm" onClick={() => act(`/api/procurement/pos/${id}/ack`, { accepted: true }, "PO acknowledged")}>
            Acknowledge Receipt
          </button>
        )}
        <div className="spacer" />
        <a className="link small" href={`/workflows?entity=PURCHASE_ORDER:${id}`}>Full workflow →</a>
      </div>
      {po.lines && (
        <>
          <hr className="divider" />
          <div className="section-title">Order Lines</div>
          <table className="table">
            <thead>
              <tr>
                <th>Line</th>
                <th>SKU</th>
                <th>Qty</th>
                <th>Unit price</th>
                <th>Received</th>
              </tr>
            </thead>
            <tbody>
              {po.lines.map((l) => (
                <tr key={l.line_no}>
                  <td>{l.line_no}</td>
                  <td className="mono">{l.sku}</td>
                  <td>{l.quantity}</td>
                  <td><Money v={l.unit_price} /></td>
                  <td>{l.received_qty ?? 0}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
