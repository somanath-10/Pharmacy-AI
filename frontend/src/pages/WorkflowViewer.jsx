import React, { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api";
import { Topbar } from "../App";
import { When } from "../ui";

const PRESETS = [
  ["PURCHASE_ORDER", "PO"], ["SALES_ORDER", "SO"], ["PRODUCTION_ORDER", "MPO"],
  ["SUPPLIER_INVOICE", "AP"], ["GRN", "GRN"], ["VENDOR", "VDR"],
];

export default function WorkflowViewer() {
  const [params] = useSearchParams();
  const [etype, setEtype] = useState(params.get("entity")?.split(":")[0] || "PURCHASE_ORDER");
  const [eid, setEid] = useState(params.get("entity")?.split(":")[1] || "");
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const load = async () => {
    if (!eid) return;
    setBusy(true);
    try {
      setData(await api(`/api/workflows/${etype}/${encodeURIComponent(eid)}`));
      setErr("");
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };
  useEffect(() => { if (eid) load(); }, [etype, eid]);

  const nodes = data?.nodes || [];
  const audit = data?.audit || [];
  const stateSet = {};
  nodes.forEach((n) => { stateSet[n.node] = n.status; });

  return (
    <div>
      <Topbar title="Workflow Viewer"
              sub="Every entity exposes its full lifecycle — agent/user, timestamps, reasons and audit records" />
      <div className="card mb">
        <div className="row" style={{ flexWrap: "wrap" }}>
          <div className="field" style={{ marginBottom: 0, minWidth: 210 }}>
            <label>Entity type</label>
            <select value={etype} onChange={(e) => setEtype(e.target.value)}>
              {PRESETS.map(([t]) => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>
          <div className="field" style={{ marginBottom: 0, minWidth: 180 }}>
            <label>Entity ID</label>
            <input value={eid} onChange={(e) => setEid(e.target.value)}
                   placeholder="e.g. PO-00001" className="mono" />
          </div>
          {PRESETS.filter(([, p]) => p === etype.slice(0, 2) || true).slice(0, 0)}
          <div className="field" style={{ marginBottom: 0 }}>
            <label>&nbsp;</label>
            <button className="btn primary" onClick={load} disabled={!eid || busy}>
              {busy ? "Loading…" : "Trace workflow"}
            </button>
          </div>
          <div className="spacer" />
          <div className="small muted" style={{ alignSelf: "end" }}>
            Try: PO-00001 · SO-00001 · MPO-00001 · AP-00001
          </div>
        </div>
      </div>

      {err && <div className="error-box mb">{err}</div>}

      {data && (
        <>
          <div className="card mb">
            <h3>{etype} {eid} — lifecycle</h3>
            <div className="card-sub">Green = done · Blue = in progress · Grey = pending</div>
            {nodes.length === 0 ? (
              <div className="empty">No workflow nodes recorded for this entity yet.</div>
            ) : (
              <div className="wf-flow">
                {nodes.map((n, i) => (
                  <React.Fragment key={n.node}>
                    {i > 0 && <span className="wf-arrow">→</span>}
                    <div className={`wf-node ${n.status === "DONE" ? "done"
                          : n.status === "ACTIVE" ? "active" : "pending"}`}>
                      <b>{n.label || n.node}</b>
                      <small>{n.status === "DONE" ? (n.detail || "✓ Done")
                             : n.status === "ACTIVE" ? "In progress…" : "Pending"}</small>
                    </div>
                  </React.Fragment>
                ))}
              </div>
            )}
          </div>

          <div className="card">
            <h3>Audit trail ({audit.length} events)</h3>
            <div className="card-sub">Append-only — every state change, actor and reason</div>
            {audit.length === 0 ? (
              <div className="empty">No audit events.</div>
            ) : (
              <table className="table">
                <thead><tr>
                  <th>When</th><th>Action</th><th>Actor</th>
                  <th>Previous → New</th><th>Reason / Policy</th>
                </tr></thead>
                <tbody>
                  {audit.map((a, i) => (
                    <tr key={i}>
                      <td className="small">{When(a.timestamp)}</td>
                      <td><span className="badge blue">{a.action?.replace(/_/g, " ")}</span></td>
                      <td className="small mono">{a.actor?.type === "AGENT" ? "🤖" : "👤"} {a.actor?.id}</td>
                      <td className="small mono">{a.previous_state || "—"} → {a.new_state || "—"}</td>
                      <td className="small">{a.reason || a.details?.reason || a.policy || ""}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}
    </div>
  );
}
