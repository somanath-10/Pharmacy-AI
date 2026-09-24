import React, { useEffect, useState } from "react";
import { api } from "../api";
import { Topbar } from "../App";
import { Badge, Table, When, useToast, CountTabs, Kpi } from "../ui";

const TABS = ["Proposals", "Demand Signals"];

export default function SupplyWorkspace() {
  const [tab, setTab] = useState("Proposals");
  const [proposals, setProposals] = useState([]);
  const [products, setProducts] = useState([]);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const { toast, toastHost } = useToast();

  const load = async () => {
    try {
      const [p, pr] = await Promise.all([
        api("/api/planning/proposals").catch(() => []),
        api("/api/masters/products"),
      ]);
      setProposals(Array.isArray(p) ? p : p.items || []);
      setProducts(Array.isArray(pr) ? pr : []);
      setErr("");
    } catch (e) { setErr(e.message); }
  };
  useEffect(() => { load(); const t = setInterval(load, 20000); return () => clearInterval(t); }, []);

  const act = async (path, body = {}, label = "Done") => {
    try { await api(path, { method: "POST", body }); toast(label, "ok"); load(); }
    catch (e) { toast(e.message, "err"); }
  };

  const runMrp = async () => {
    setBusy(true);
    try {
      await api("/api/planning/mrp/run", { method: "POST", body: {} });
      toast("MRP run complete — proposals generated", "ok");
      setTab("Proposals");
      load();
    } catch (e) { toast(e.message, "err"); } finally { setBusy(false); }
  };

  const decide = (p, decision) =>
    act(`/api/planning/proposals/${p.proposal_id}/decide`, { decision },
        `Proposal ${decision.toLowerCase()}`);

  const open = proposals.filter((p) => p.status === "PROPOSED").length;
  const auto = proposals.filter((p) => p.status === "AUTO_EXECUTED").length;
  const belowRop = products.filter((p) =>
    p.reorder_point != null && (p.available_qty ?? 0) <= p.reorder_point).length;

  const ACTION_TONE = { TRANSFER: "blue", PRODUCE: "purple", BUY: "orange" };

  return (
    <div>
      {toastHost}
      <Topbar title="Supply Chain Planning"
              sub="Demand → stock check → transfer vs produce vs buy — the Supply Chain Agent decides, humans approve exceptions" />
      {err && <div className="error-box mb">{err}</div>}

      <div className="grid kpi-4 mb">
        <Kpi ico="🧮" label="Open proposals" value={open} tone={open ? "orange" : "green"} />
        <Kpi ico="⚡" label="Auto-executed" value={auto} tone="green" />
        <Kpi ico="📉" label="SKUs below reorder" value={belowRop} tone={belowRop ? "red" : "green"} />
        <Kpi ico="📦" label="Products planned" value={products.length} tone="blue" />
      </div>

      <div className="row mb wrap">
        <CountTabs tabs={TABS.map((t) => ({
          label: t,
          count: t === "Proposals" ? open : belowRop,
        }))} active={tab} onChange={setTab} />
        <div className="spacer" />
        <button className="btn primary" onClick={runMrp} disabled={busy}>
          {busy ? <span className="spin" /> : "▶"} Run MRP now
        </button>
        <button className="btn ghost" onClick={() =>
          act("/api/agents/run/supply-chain-agent", { goal: "rebalance" }, "Supply chain agent run started")}>
          🤖 Run Supply Chain Agent
        </button>
      </div>

      <div className="tab-panel" key={tab}>
        {tab === "Proposals" && (
          <div className="card">
            <h3>Planning proposals</h3>
            <div className="card-sub">TRANSFER (another warehouse has excess) · PRODUCE (capacity available) · BUY (procure). Routine proposals auto-execute; only anomalies and strategic value reach this queue.</div>
            <Table rows={proposals} empty="No proposals — run MRP to generate the plan"
              columns={[
                { key: "proposal_id", label: "ID", render: (r) => <span className="mono">{r.proposal_id}</span> },
                { key: "product_id", label: "SKU", render: (r) => <span className="mono">{r.product_id}</span> },
                { key: "action", label: "Action", render: (r) => (
                  <span className={`badge ${ACTION_TONE[r.action] || "gray"}`}>{r.action}</span>
                )},
                { key: "quantity", label: "Qty" },
                { key: "reason", label: "Why", render: (r) => <span className="small">{r.reason || r.rationale || "—"}</span> },
                { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                { key: "created_at", label: "Proposed", render: (r) => <span className="small">{When(r.created_at)}</span> },
                { key: "act", label: "", render: (r) => r.status === "PROPOSED" ? (
                  <span className="row" style={{ gap: 6 }}>
                    <button className="btn approve sm" onClick={() => decide(r, "APPROVED")}>Accept</button>
                    <button className="btn reject sm" onClick={() => decide(r, "REJECTED")}>Reject</button>
                  </span>
                ) : null },
              ]} />
          </div>
        )}

        {tab === "Demand Signals" && (
          <div className="card">
            <h3>Demand signals (stock vs reorder point)</h3>
            <div className="card-sub">Safety stock and reorder points drive the agent's shortage detection</div>
            <Table rows={products} empty="No products"
              columns={[
                { key: "sku", label: "SKU", render: (r) => <span className="mono">{r.sku}</span> },
                { key: "name", label: "Product" },
                { key: "type", label: "Type", render: (r) => <Badge>{r.type}</Badge> },
                { key: "safety_stock", label: "Safety stock" },
                { key: "reorder_point", label: "Reorder point" },
                { key: "signal", label: "Signal", render: (r) =>
                  r.reorder_point != null && (r.available_qty ?? 0) <= r.reorder_point
                    ? <span className="badge red">below ROP</span>
                    : <span className="badge green">healthy</span> },
              ]} />
          </div>
        )}
      </div>
    </div>
  );
}
