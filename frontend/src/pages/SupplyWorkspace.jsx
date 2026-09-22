import React, { useEffect, useState } from "react";
import { api } from "../api";
import { Topbar } from "../App";
import { Badge, Table, When, useToast } from "../ui";

export default function SupplyWorkspace() {
  const [proposals, setProposals] = useState([]);
  const [products, setProducts] = useState([]);
  const [err, setErr] = useState("");
  const toast = useToast();

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
    try { await api(path, { method: "POST", body }); toast(label); load(); }
    catch (e) { toast(e.message, "err"); }
  };

  const decide = (p, decision) =>
    act(`/api/planning/proposals/${p.proposal_id}/decide`, { decision },
        `Proposal ${decision.toLowerCase()}`);

  return (
    <div>
      <Topbar title="Supply Chain Planning"
              sub="Demand → stock check → transfer vs produce vs buy — the Supply Chain Agent decides, humans approve exceptions" />
      {err && <div className="error-box mb">{err}</div>}
      <div className="row mb">
        <button className="btn primary" onClick={() =>
          act("/api/planning/mrp/run", {}, "MRP run complete — proposals generated")}>
          ▶ Run MRP now
        </button>
        <button className="btn ghost" onClick={() =>
          act("/api/agents/run/supply-chain-agent", { goal: "rebalance" }, "Supply chain agent run started")}>
          🤖 Run Supply Chain Agent
        </button>
      </div>

      <div className="card mb">
        <h3>Planning proposals</h3>
        <div className="card-sub">TRANSFER (another warehouse has excess) · PRODUCE (capacity available) · BUY (procure)</div>
        <Table rows={proposals} empty="No proposals — run MRP"
          columns={[
            { key: "proposal_id", label: "ID", render: (r) => <span className="mono">{r.proposal_id}</span> },
            { key: "product_id", label: "SKU", render: (r) => <span className="mono">{r.product_id}</span> },
            { key: "action", label: "Action", render: (r) => (
              <span className={`badge ${r.action === "TRANSFER" ? "blue" : r.action === "PRODUCE" ? "purple" : "orange"}`}>
                {r.action}
              </span>
            )},
            { key: "quantity", label: "Qty" },
            { key: "reason", label: "Why", render: (r) => <span className="small">{r.reason || r.rationale || "—"}</span> },
            { key: "status", label: "Status", render: (r) => <Badge value={r.status} /> },
            { key: "created_at", label: "Proposed", render: (r) => When(r.created_at) },
            { key: "act", label: "", render: (r) => r.status === "PROPOSED" ? (
              <span className="row" style={{ gap: 6 }}>
                <button className="btn approve sm" onClick={() => decide(r, "APPROVED")}>Accept</button>
                <button className="btn reject sm" onClick={() => decide(r, "REJECTED")}>Reject</button>
              </span>
            ) : null },
          ]} />
      </div>

      <div className="card">
        <h3>Demand signals (stock vs reorder point)</h3>
        <Table rows={products} empty="No products"
          columns={[
            { key: "sku", label: "SKU", render: (r) => <span className="mono">{r.sku}</span> },
            { key: "name", label: "Product" },
            { key: "type", label: "Type", render: (r) => <Badge value={r.type} /> },
            { key: "safety_stock", label: "Safety stock" },
            { key: "reorder_point", label: "Reorder point" },
          ]} />
      </div>
    </div>
  );
}
