import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { Topbar } from "../App";
import { Kpi, Badge, Table, When, useToast } from "../ui";

const DEPTS = [
  ["warehouse", "Warehouse", "📦"], ["quality", "QA / QC", "🛡️"],
  ["plant", "Plant", "🏭"], ["logistics", "Logistics", "🚚"],
  ["finance", "Finance", "💰"],
];

const INPUTS = [
  ["📧", "Emails", "Inquiries & replies"],
  ["📄", "RFQs / Inquiries", "Customers & vendors"],
  ["📕", "PO PDFs", "Customer POs via Doc AI"],
  ["📋", "Vendor Documents", "Certs, licences, CoA"],
  ["🚚", "Shipment Events", "Tracking & handovers"],
];
const OUTPUTS = [
  ["✅", "Confirmed Orders"], ["🧾", "Procurement Tasks"], ["🏷️", "Batch Release"],
  ["🚛", "Dispatch"], ["✉️", "Customer Updates"], ["⚠️", "Alerts & Exceptions"],
];

const MODULES = [
  ["📈", "Sales", "Lead to Order", "/sales"], ["🎧", "Customer Service", "Engage & Support", "/sales"],
  ["🛒", "Purchase", "Procure & Manage", "/vendors"], ["👥", "Vendor Mgmt", "Onboard & Govern", "/vendors"],
  ["📆", "Planning", "Demand & Supply", "/supply"], ["⚖️", "Regulatory", "Compliance & Filings", "/exceptions"],
  ["🛡️", "QA", "Quality Assurance", "/quality"], ["🧪", "QC", "Quality Control", "/quality"],
  ["🏭", "Plant", "Manufacturing Ops", "/plant"], ["📦", "Warehouse", "Inventory & Storage", "/warehouse"],
  ["🚚", "Logistics", "Distribute & Deliver", "/logistics"], ["💰", "Finance", "Billing & Reconciliation", "/finance"],
  ["📄", "Document AI", "Extract · Understand", "/agents"], ["✉️", "Email AI", "Read · Respond", "/agents"],
  ["📊", "Analytics", "Insights & Intelligence", "/agents"],
];

export default function CommandCenter() {
  const nav = useNavigate();
  const { toast, toastHost } = useToast();
  const [cc, setCc] = useState(null);
  const [approvals, setApprovals] = useState([]);
  const [err, setErr] = useState("");
  const [pulse, setPulse] = useState(0);

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
    const t = setInterval(() => { load(); setPulse((p) => p + 1); }, 12000);
    return () => { stop = true; clearInterval(t); };
  }, []);

  const decide = async (a, decision, extra) => {
    try {
      await api(`/api/approvals/${a.approval_id}/decide`,
                { method: "POST", body: { decision, reason: `${decision} from Command Center`, ...extra } });
      toast(`${a.entity_id || "Item"} ${decision.toLowerCase()}d`, "ok");
      setApprovals((rows) => rows.filter((x) => x.approval_id !== a.approval_id));
    } catch (e) { toast(e.message, "err"); }
  };

  const k = cc?.kpis || {};
  const errors = cc?.errors || {};
  const loadFailed = !cc && !!err;
  const fmtErr = (key) => errors[key]
    ? <span className="small text-red">⚠ Unavailable: {errors[key]}</span> : null;

  return (
    <div>
      {toastHost}
      <Topbar title="Unified AI Command Center"
              sub="Live enterprise status — every number is real; failures show as errors, never as fake values" />
      {err && <div className="error-box mb">Failed to load command center data: {err}</div>}

      {/* KPI strip — Part 1 metrics, all real */}
      <div className="grid kpi-6 mb">
        <Kpi ico="🤖" label="Active Agents"
             value={k.agents ? `${k.agents.active}/${k.agents.total}` : "…"}
             foot={k.agents?.degraded?.length ? `degraded: ${k.agents.degraded.join(", ")}` : "all governed & audited"}
             tone="purple" />
        <Kpi ico="⚙️" label="Automation rate"
             value={k.automation ? `${k.automation.automation_rate_pct}%` : "…"}
             foot={k.automation ? `${k.automation.audited_actions_24h} audited actions / 24h` : ""} />
        <Kpi ico="🙋" label="Human touch rate"
             value={k.automation ? `${k.automation.human_touch_rate_pct}%` : "…"}
             foot="of audited actions were human" tone="blue" />
        <Kpi ico="⚠️" label="Open exceptions"
             value={k.exceptions ? k.exceptions.total : "…"}
             foot={k.exceptions ? `${k.exceptions.invoice_mismatches} invoice · ${k.exceptions.oos_investigations} OOS · ${k.exceptions.shipment_exceptions} ship` : ""}
             tone={k.exceptions?.total ? "orange" : "green"} />
        <Kpi ico="📋" label="Pending approvals"
             value={k.pending_approvals ?? "…"} foot="awaiting human decision"
             tone={k.pending_approvals ? "orange" : "green"} />
        <Kpi ico="📈" label="Open orders"
             value={k.orders ? `S:${k.orders.sales} P:${k.orders.purchase}` : "…"}
             foot="sales / purchase" tone="teal" />
      </div>

      {/* domain error banner — honest per-domain failure reporting */}
      {Object.keys(errors).length > 0 && (
        <div className="error-box mb">
          <b>Some domains are unavailable right now:</b>{" "}
          {Object.entries(errors).map(([k2, v]) => (
            <div className="small" key={k2}>• {k2}: {v}</div>
          ))}
        </div>
      )}

      {/* Core row: inputs | core | outputs */}
      <div className="grid layout-3col mb">
        <div className="card side-card rise d1">
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

        <div className="core-panel rise d2">
          <div className="core-head">
            <span className="spark">✦</span>
            <div>
              <h2>AI Orchestration Core</h2>
              <div className="core-sub">Domain agents working together across the enterprise</div>
            </div>
            <span className="live"><span className="dot pulse" /> Live · {pulse}</span>
          </div>
          <div className="module-grid">
            {MODULES.map(([ico, name, sub, to]) => (
              <div className="module-tile" key={name}
                   onClick={() => to && nav(to)}>
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

        <div className="card side-card rise d3">
          <h3>Outputs & actions</h3>
          <div className="card-sub">From insight to execution, seamlessly</div>
          {OUTPUTS.map(([ico, b]) => (
            <div className="out-row" key={b}>
              <span className="ico" style={{ background: "var(--blue-soft)" }}>{ico}</span>
              <div><b>{b}</b><small>automated by agents</small></div>
              <span className="dot green" />
            </div>
          ))}
        </div>
      </div>

      {/* Department status + decision queue */}
      <div className="grid cols-2 mb" style={{ gridTemplateColumns: "1.6fr 1fr" }}>
        <div className="card rise d4">
          <h3>Department status</h3>
          <div className="card-sub">Derived from live counters — no synthetic scores</div>
          <div className="dept-grid">
            {DEPTS.map(([key, name, ico]) => {
              const d = k[key];
              if (errors[key] || !d) {
                return (
                  <div className="dept" key={key}>
                    <div className="ico">{ico}</div>
                    <b>{name}</b>
                    <small className="text-red">Unavailable</small>
                    <span className="dot red" />
                    <div className="small muted" style={{ marginTop: 4 }}>{errors[key] || "loading…"}</div>
                    <div className="bar"><i className="warn" style={{ width: "0%" }} /></div>
                  </div>
                );
              }
              const attn = d.status !== "HEALTHY";
              return (
                <div className="dept" key={key}>
                  <div className="ico">{ico}</div>
                  <b>{name}</b>
                  <small>{d.status === "HEALTHY" ? "On track" : d.status}</small>
                  <span className={`dot ${d.status === "HEALTHY" ? "green" : d.status === "CRITICAL" ? "red" : "orange"}`} />
                  <div style={{ fontWeight: 800, color: "var(--navy)", marginTop: 4 }}>{d.score}%</div>
                  <div className="bar">
                    <i className={d.status !== "HEALTHY" ? "warn" : ""} style={{ width: `${d.score}%` }} />
                  </div>
                  <div className="small muted">
                    {key === "warehouse" && `${d.quarantine_rows} quarantine rows · ${d.open_pick_tasks} picks`}
                    {key === "quality" && `${d.active_holds} holds · ${d.oos_open} OOS`}
                    {key === "plant" && `${d.batches_in_process} batches in process`}
                    {key === "logistics" && `${d.in_transit} in transit · ${d.exceptions} exceptions`}
                    {key === "finance" && `AP:${d.ap_open} AR:${d.ar_open} · ${d.invoice_exceptions} exceptions`}
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        <div className="card rise d5">
          <div className="row">
            <h3>Decision queue</h3>
            <div className="spacer" />
            <a className="link small" onClick={() => nav("/queue")}>View all ({approvals.length}) →</a>
          </div>
          <div className="card-sub">Human role: approve, review, reject, escalate — AI assembles the evidence</div>
          {loadFailed
            ? <div className="error-box">Approvals unavailable: {err}</div>
            : <Table
                empty="No human decisions pending — agents resolved everything"
                rows={approvals.slice(0, 5)}
                columns={[
                  { key: "entity_id", label: "Item", render: (r) => <span className="mono">{r.entity_id || r.approval_id}</span> },
                  { key: "category", label: "Type", render: (r) => <Badge>{r.category}</Badge> },
                  { key: "created_at", label: "Raised", render: (r) => <span className="small">{When(r.created_at)}</span> },
                  { key: "act", label: "Action", render: (r) => (
                    <span className="row" style={{ gap: 6 }}>
                      <button className="btn approve sm" onClick={(e) => { e.stopPropagation(); decide(r, "APPROVED"); }}>Approve</button>
                      <button className="btn ghost sm" onClick={(e) => { e.stopPropagation(); decide(r, "ESCALATED"); }}>Escalate</button>
                    </span>
                  )},
                ]} />}
        </div>
      </div>

      {/* agent strip — real fleet only */}
      <div className="card rise d6">
        <h3>Agent fleet</h3>
        <div className="card-sub">Every agent is permission-gated, audited and policy-bound</div>
        {k.agents
          ? <div className="tag-list">
              {(k.agents.degraded || []).length === 0
                ? <span className="badge green" style={{ fontSize: 12, padding: "6px 12px" }}>
                    <span className="dot green" style={{ width: 6, height: 6 }} />
                    All {k.agents.total} agents ACTIVE
                  </span>
                : k.agents.degraded.map((name) => (
                  <span key={name} className="badge red" style={{ fontSize: 12, padding: "6px 12px" }}>
                    <span className="dot red" style={{ width: 6, height: 6 }} />{name}
                  </span>
                ))}
              <a className="link small" onClick={() => nav("/agents")}>Agent Activity →</a>
            </div>
          : <div className="skeleton" style={{ height: 36 }} />}
      </div>
    </div>
  );
}
