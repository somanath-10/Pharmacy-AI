import React, { useEffect, useState } from "react";
import { api } from "../api";
import { Topbar } from "../App";
import { Badge, Table, When, Kpi, Empty } from "../ui";

export default function ExecutiveDashboard() {
  const [data, setData] = useState(null);
  const [ai, setAi] = useState(null);
  const [err, setErr] = useState("");

  const load = async () => {
    try {
      const [d, u] = await Promise.all([
        api("/api/analytics/executive"),
        api("/api/ai/usage").catch(() => null),
      ]);
      setData(d);
      setAi(u);
      setErr("");
    } catch (e) { setErr(e.message); }
  };
  useEffect(() => { load(); const t = setInterval(load, 30000); return () => clearInterval(t); }, []);

  if (err) return (
    <div>
      <Topbar title="Executive Dashboard" sub="Management view — all domains, AI governance, business risks" />
      <div className="error-box">Failed to load executive data: {err}</div>
    </div>
  );
  if (!data) return (
    <div>
      <Topbar title="Executive Dashboard" sub="Management view" />
      <div className="card"><div className="skeleton" style={{ height: 240 }} /></div>
    </div>
  );

  const k = (v) => (v == null ? "—" : v);
  const money = (v) => v == null ? "—" :
    `₹${v >= 1e7 ? `${(v / 1e7).toFixed(1)}Cr` : v >= 1e5 ? `${(v / 1e5).toFixed(1)}L` : v.toLocaleString()}`;

  return (
    <div>
      <Topbar title="Executive Dashboard"
              sub="One screen for management: commercial, operations, quality, finance, AI governance and business risks" />
      {err && <div className="error-box mb">{err}</div>}

      <div className="grid kpi-6 mb">
        <Kpi ico="📈" label="Open sales" value={k(data.sales.open_orders)}
             foot={money(data.sales.open_value)} tone="blue" />
        <Kpi ico="🛒" label="Open purchase" value={k(data.purchase.open_orders)}
             foot={money(data.purchase.open_value)} tone="teal" />
        <Kpi ico="🏭" label="Batches in process" value={k(data.production.batches_in_process)} tone="purple" />
        <Kpi ico="🧪" label="Quality" value={`${k(data.quality.active_holds)}H / ${k(data.quality.oos_open)}OOS`}
             foot="holds / OOS open" tone={data.quality.oos_open ? "orange" : "green"} />
        <Kpi ico="🚚" label="Logistics" value={k(data.warehouse.in_transit_shipments)}
             foot={`${k(data.warehouse.exceptions)} exceptions`}
             tone={data.warehouse.exceptions ? "orange" : "green"} />
        <Kpi ico="🤖" label="Automation rate" value={`${k(data.ai.automation_rate_pct)}%`}
             foot={`${k(data.ai.human_decisions_pending)} human decisions pending`} tone="purple" />
      </div>

      <div className="grid cols-2 mb">
        <div className="card rise">
          <h3>Business risks</h3>
          <div className="card-sub">Deterministic rules over live data — no hidden exposure</div>
          {(data.risks || []).length === 0
            ? <Empty art="🛡️" title="No active business risks flagged" />
            : <Table rows={data.risks} empty="—"
                columns={[
                  { key: "risk", label: "Risk", render: (r) => <b>{r.risk}</b> },
                  { key: "level", label: "Level", render: (r) => (
                    <Badge>{r.level}</Badge>) },
                  { key: "count", label: "Count", render: (r) => r.count ?? "—" },
                ]} />}
        </div>
        <div className="card rise d1">
          <h3>AI governance</h3>
          <div className="card-sub">Agent fleet health and automation economics</div>
          <Table rows={data.ai.agents || []} empty="—"
            columns={[
              { key: "agent", label: "Agent", render: (r) => <b>{r.agent_id}</b> },
              { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
              { key: "runs", label: "Runs" },
              { key: "errors", label: "Errors", render: (r) => (
                <span className={r.errors ? "text-red" : ""}>{r.errors}</span>) },
            ]} />
          {ai && (
            <div className="mt small muted">
              AI spend (metered): <b>${ai.total_estimated_cost_usd}</b> across {ai.days?.length || 0} day(s)
            </div>
          )}
        </div>
      </div>

      <div className="grid kpi-4">
        <Kpi ico="📦" label="Products" value={k(data.inventory.products)} />
        <Kpi ico="⚠️" label="Quarantine/hold rows" value={k(data.inventory.quarantine_or_hold_rows)}
             tone={data.inventory.quarantine_or_hold_rows ? "orange" : "green"} />
        <Kpi ico="📥" label="AP open" value={k(data.finance.ap_open)} tone="blue" />
        <Kpi ico="📤" label="AR open" value={k(data.finance.ar_open)} tone="purple" />
      </div>
    </div>
  );
}
