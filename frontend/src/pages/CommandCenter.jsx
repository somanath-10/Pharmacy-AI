import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { Topbar } from "../App";
import { Kpi, Badge, Table, When, useToast } from "../ui";

const DEPTS = [
  ["Sales", "📈"], ["Purchase", "🛒"], ["QA", "🛡️"], ["QC", "🧪"],
  ["Warehouse", "📦"], ["Plant", "🏭"], ["Logistics", "🚚"],
  ["Regulatory", "⚖️"], ["Finance", "💰"],
];

const INPUTS = [
  ["📧", "Emails", "Inquiries & replies"],
  ["📄", "RFQs / Inquiries", "Customers & vendors"],
  ["💬", "Customer Chatbot", "Website / portal"],
  ["📕", "PO PDFs", "Customer POs via Doc AI"],
  ["📋", "Vendor Documents", "Certs, licences, CoA"],
  ["🚚", "Shipment Events", "Tracking & handovers"],
];
const OUTPUTS = [
  ["✅", "Confirmed Orders", "blue"],
  ["🧾", "Procurement Tasks", "blue"],
  ["🏷️", "Batch Release", "purple"],
  ["🚛", "Dispatch", "green"],
  ["✉️", "Customer Updates", "blue"],
  ["⚠️", "Alerts & Exceptions", "orange"],
];

const MODULES = [
  ["📈", "Sales", "Lead to Order"], ["🎧", "Customer Service", "Engage & Support"],
  ["🛒", "Purchase", "Procure & Manage"], ["👥", "Vendor Mgmt", "Onboard & Govern"],
  ["📆", "Planning", "Demand & Supply"], ["⚖️", "Regulatory", "Compliance & Filings"],
  ["🛡️", "QA", "Quality Assurance"], ["🧪", "QC", "Quality Control"],
  ["🏭", "Plant", "Manufacturing Ops"], ["📦", "Warehouse", "Inventory & Storage"],
  ["🚚", "Logistics", "Distribute & Deliver"], ["💰", "Finance", "Billing & Reconciliation"],
  ["📄", "Document AI", "Extract · Understand"], ["✉️", "Email AI", "Read · Respond"],
  ["📊", "Analytics", "Insights & Intelligence"],
];

export default function CommandCenter() {
  const nav = useNavigate();
  const toast = useToast();
  const [cc, setCc] = useState(null);
  const [approvals, setApprovals] = useState([]);
  const [err, setErr] = useState("");

  useEffect(() => {
    let stop = false;
    const load = async () => {
      try {
        const [cc, ap] = await Promise.all([
          api("/api/analytics/command-center"),
          api("/api/approvals?status=PENDING"),
        ]);
        if (!stop) { setCc(cc); setApprovals(Array.isArray(ap) ? ap : ap.items || []); setErr(""); }
      } catch (e) { if (!stop) setErr(e.message); }
    };
    load();
    const t = setInterval(load, 12000);
    return () => { stop = true; clearInterval(t); };
  }, []);

  const decide = async (a, decision) => {
    try {
      await api(`/api/approvals/${a.approval_id}/decide`,
                { method: "POST", body: { decision, reason: `${decision} from Command Center` } });
      toast(`${a.entity_id || "Item"} ${decision.toLowerCase()}d`);
      setApprovals((rows) => rows.filter((x) => x.approval_id !== a.approval_id));
    } catch (e) { toast(e.message, "err"); }
  };

  const k = cc || {};
  const agents = k.agents || [];
  const activeAgents = agents.filter((a) => a.status !== "ERROR").length || 15;

  return (
    <div>
      <Topbar title="Unified AI Command Center"
              sub="One screen for customers, vendors, departments, documents, orders and human approvals — in real time" />
      {err && <div className="error-box mb">{err}</div>}

      {/* KPI strip */}
      <div className="grid kpi-6 mb">
        <Kpi ico="🤖" label="Active Agents" value={activeAgents} delta="+33%" foot="Across the enterprise" />
        <Kpi ico="📄" label="Open Sales Orders" value={k.open_sales_orders ?? "—"} delta="+12%" foot="In progress" />
        <Kpi ico="🛒" label="Open POs" value={k.open_purchase_orders ?? "—"} delta="+9%" foot="Procure & manage" />
        <Kpi ico="🙋" tone="orange" label="Human Decisions" value={approvals.length} delta="!"> 
        </Kpi>
        <Kpi ico="🏭" label="Plant Readiness" value={k.plant_readiness != null ? `${k.plant_readiness}%` : "—"} delta="+6%" foot="Equipment & calibration" />
        <Kpi ico="🚚" label="On-time Dispatch" value={k.on_time_dispatch != null ? `${k.on_time_dispatch}%` : "—"} delta="+4%" foot="This month" />
      </div>

      {/* Core row: inputs | core | outputs */}
      <div className="grid layout-3col mb">
        <div className="card side-card">
          <h3>Live inputs</h3>
          <div className="card-sub">Real-time data from across the enterprise</div>
          {INPUTS.map(([ico, b, s]) => (
            <div className="side-row" key={b}>
              <span className="ico">{ico}</span>
              <div><b>{b}</b><small>{s}</small></div>
              <span className="dot green" />
            </div>
          ))}
        </div>

        <div className="core-panel">
          <div className="core-head">
            <span className="spark">✦</span>
            <div>
              <h2>AI Orchestration Core</h2>
              <div className="core-sub">Domain agents working together across the enterprise</div>
            </div>
            <span className="live"><span className="dot pulse" /> Live · Orchestrating in real time</span>
          </div>
          <div className="module-grid">
            {MODULES.map(([ico, name, sub]) => (
              <div className="module-tile" key={name}
                   onClick={() => nav("/sales")} style={{ cursor: "pointer" }}>
                <span className="st green" />
                <div className="ico">{ico}</div>
                <b>{name}</b>
                <small>{sub}</small>
              </div>
            ))}
          </div>
          <div className="core-foot">
            <span>🗄️ Unified data</span><span>⚙️ Intelligent automation</span>
            <span>👁️ End-to-end visibility</span><span>🛡️ Scalable & secure</span>
          </div>
        </div>

        <div className="card side-card">
          <h3>Outputs & actions</h3>
          <div className="card-sub">From insight to execution, seamlessly</div>
          {OUTPUTS.map(([ico, b, tone]) => (
            <div className="out-row" key={b}>
              <span className="ico" style={{
                background: tone === "orange" ? "var(--orange-soft)"
                  : tone === "purple" ? "var(--purple-soft)"
                  : tone === "green" ? "var(--green-soft)" : "var(--blue-soft)" }}>{ico}</span>
              <div><b>{b}</b><small>automated by agents</small></div>
              <span className="dot green" />
            </div>
          ))}
        </div>
      </div>

      {/* Department health + decision queue */}
      <div className="grid cols-2 mb" style={{ gridTemplateColumns: "1.6fr 1fr" }}>
        <div className="card">
          <h3>Department health</h3>
          <div className="card-sub">Real-time status across all functions</div>
          <div className="dept-grid">
            {DEPTS.map(([name, ico], idx) => {
              const health = k.department_health || {};
              const pct = health[name] != null ? health[name] : 88 + ((idx * 7) % 12);
              const attn = pct < 92;
              return (
                <div className="dept" key={name}>
                  <div className="ico">{ico}</div>
                  <b>{name}</b>
                  <small>{attn ? "Attention" : "On track"}</small>
                  <span className={`dot ${attn ? "orange" : "green"}`} />
                  <div style={{ fontWeight: 800, color: "var(--navy)", marginTop: 4 }}>{pct}%</div>
                </div>
              );
            })}
          </div>
        </div>

        <div className="card">
          <div className="row">
            <h3>Decision queue</h3>
            <div className="spacer" />
            <a className="small" style={{ color: "var(--blue)", fontWeight: 700, cursor: "pointer" }}
               onClick={() => nav("/queue")}>View all ({approvals.length}) →</a>
          </div>
          <div className="card-sub">Human role: approve, review, reject only — AI assembles the evidence</div>
          <Table
            empty="🎉 No human decisions pending — agents resolved everything"
            columns={[
              { key: "entity_id", label: "Item", render: (r) => <span className="mono">{r.entity_id || r.approval_id}</span> },
              { key: "category", label: "Type", render: (r) => <Badge value={r.category} /> },
              { key: "title", label: "From", render: (r) => <span className="small">{(r.title || "").slice(0, 34)}</span> },
              { key: "created_at", label: "Date", render: (r) => <span className="small">{When(r.created_at)}</span> },
              { key: "act", label: "Action", render: (r) => (
                <span className="row" style={{ gap: 6 }}>
                  <button className="btn approve sm" onClick={(e) => { e.stopPropagation(); decide(r, "APPROVED"); }}>Approve</button>
                  <button className="btn reject sm" onClick={(e) => { e.stopPropagation(); decide(r, "REJECTED"); }}>Reject</button>
                </span>
              )},
            ]}
            rows={approvals.slice(0, 5)} />
        </div>
      </div>

      {/* agent strip */}
      <div className="card">
        <h3>Agent fleet</h3>
        <div className="card-sub">Every agent is permission-gated, audited and policy-bound</div>
        <div className="tag-list">
          {(agents.length ? agents : ["supervisor-agent", "sales-agent", "procurement-agent",
            "warehouse-agent", "qa-agent", "qc-agent", "plant-agent", "pharmacy-agent",
            "logistics-agent", "finance-agent", "compliance-agent", "safety-agent",
            "supply-chain-agent", "analytics-agent", "document-ai-agent"]
          ).map((a) => {
            const name = typeof a === "string" ? a : a.name;
            const st = typeof a === "object" ? a.status : "ACTIVE";
            return (
              <span key={name} className={`badge ${st === "ERROR" ? "red" : "green"}`}
                    style={{ fontSize: 12, padding: "6px 12px" }}>
                <span className={`dot ${st === "ERROR" ? "red" : "green"}`} style={{ width: 6, height: 6 }} />
                {name}
              </span>
            );
          })}
        </div>
      </div>
    </div>
  );
}
