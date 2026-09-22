import React, { useEffect, useState } from "react";
import { api } from "../api";
import { Topbar } from "../App";
import { Badge, Table, When, useToast } from "../ui";

const CATS = ["ALL", "FINANCIAL_AUTHORITY", "STRATEGIC", "REGULATORY_EXCEPTION",
              "SECURITY_FRAUD", "QUALITY", "UNRESOLVED_EXCEPTION"];

export default function DecisionQueue() {
  const [rows, setRows] = useState([]);
  const [cat, setCat] = useState("ALL");
  const [sel, setSel] = useState(null);
  const [err, setErr] = useState("");
  const toast = useToast();

  const load = async () => {
    try {
      const r = await api("/api/approvals?status=PENDING");
      setRows(Array.isArray(r) ? r : r.items || []);
      setErr("");
    } catch (e) { setErr(e.message); }
  };
  useEffect(() => { load(); const t = setInterval(load, 10000); return () => clearInterval(t); }, []);

  const decide = async (decision) => {
    if (!sel) return;
    try {
      await api(`/api/approvals/${sel.approval_id}/decide`,
                { method: "POST", body: { decision, reason: `${decision} via Decision Queue` } });
      toast(`${sel.entity_id || "Item"} ${decision.toLowerCase()}d`, "ok");
      setSel(null);
      load();
    } catch (e) { toast(e.message, "err"); }
  };

  const filtered = cat === "ALL" ? rows : rows.filter((r) => r.category === cat);

  return (
    <div>
      <Topbar title="Human Decision Queue"
              sub="Humans approve, review and reject — agents do the routine work and assemble the evidence" />
      {err && <div className="error-box mb">{err}</div>}
      <div className="grid" style={{ gridTemplateColumns: "1.7fr 1fr" }}>
        <div className="card">
          <div className="row mb">
            <h3>Pending decisions ({filtered.length})</h3>
            <div className="spacer" />
            {CATS.map((c) => (
              <button key={c} className={`btn sm ${cat === c ? "primary" : "ghost"}`}
                      onClick={() => setCat(c)}>{c.replace(/_/g, " ")}</button>
            ))}
          </div>
          <Table
            empty="Queue is clear — agents have resolved everything 🎉"
            rows={filtered}
            onRow={setSel}
            columns={[
              { key: "approval_id", label: "ID", render: (r) => <span className="mono">{r.approval_id}</span> },
              { key: "title", label: "Item", render: (r) => <span>{r.title}</span> },
              { key: "category", label: "Category", render: (r) => <Badge value={r.category} /> },
              { key: "entity_id", label: "Entity", render: (r) => <span className="mono">{r.entity_type} {r.entity_id}</span> },
              { key: "created_at", label: "Raised", render: (r) => When(r.created_at) },
              { key: "act", label: "", render: (r) => (
                <span className="row" style={{ gap: 6 }}>
                  <button className="btn approve sm"
                          onClick={(e) => { e.stopPropagation(); setSel(r); }}>Review</button>
                </span>
              )},
            ]} />
        </div>

        <div className="card">
          {!sel ? (
            <div className="empty">Select an item to review.<br />Evidence, policy results and links are assembled by the agents.</div>
          ) : (
            <div>
              <h3>{sel.title}</h3>
              <div className="card-sub mono">{sel.approval_id} · {sel.entity_type} {sel.entity_id}</div>
              <div className="row mb">
                <Badge value={sel.category} />
                <Badge value={sel.status} />
              </div>
              <div className="field">
                <label>Evidence (assembled by AI)</label>
                <pre className="mono" style={{ background: "var(--blue-soft)", padding: 12,
                      borderRadius: 10, overflow: "auto", maxHeight: 300, fontSize: 11.5 }}>
                  {JSON.stringify(sel.evidence, null, 2)}
                </pre>
              </div>
              {sel.workflow_link && (
                <a className="small" style={{ color: "var(--blue)" }}
                   href={`/workflows?entity=${sel.entity_type}:${sel.entity_id}`}>
                  View full workflow →
                </a>
              )}
              <div className="row mt">
                <button className="btn approve" onClick={() => decide("APPROVED")}>✓ Approve</button>
                <button className="btn reject" onClick={() => decide("REJECTED")}>✗ Reject</button>
                <div className="spacer" />
                <button className="btn ghost sm" onClick={() => setSel(null)}>Close</button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
