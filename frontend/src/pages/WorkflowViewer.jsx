import React, { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "../api";
import { Topbar } from "../App";
import { When, Timeline, Field } from "../ui";

const PRESETS = [
  ["PURCHASE_ORDER", "PO"], ["PURCHASE_REQUISITION", "PR"], ["SALES_ORDER", "SO"],
  ["PRODUCTION_ORDER", "MPO"], ["SUPPLIER_INVOICE", "AP"], ["CUSTOMER_INVOICE", "AR"],
  ["GRN", "GRN"], ["VENDOR", "VDR"], ["SHIPMENT", "SHP"],
  ["PRESCRIPTION", "RX"], ["QC_SAMPLE", "QC"], ["RETURN_REQUEST", "RTN"],
  ["RECALL", "RCL"], ["SAFETY_CASE", "PV"], ["COMPLAINT", "CMP"],
  ["PAYMENT", "PAY"], ["SOURCING_EVENT", "RFQ"], ["QUOTATION", "QT"],
  ["CAPA", "CAPA"], ["DEVIATION", "DEV"], ["CHANGE_REQUEST", "CHG"],
  ["AUCTION", "AUC"], ["RAW_MATERIAL_BATCH", "RMB"],
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

  return (
    <div>
      <Topbar title="Workflow Viewer"
              sub="Every entity exposes its full lifecycle — agent/user, timestamps, reasons and audit records" />

      <div className="card mb rise">
        <div className="row wrap" style={{ alignItems: "flex-end" }}>
          <div style={{ minWidth: 220 }}>
            <Field label="Entity type">
              <select value={etype} onChange={(e) => setEtype(e.target.value)}>
                {PRESETS.map(([t]) => <option key={t} value={t}>{t}</option>)}
              </select>
            </Field>
          </div>
          <div style={{ minWidth: 180 }}>
            <Field label="Entity ID">
              <input value={eid} onChange={(e) => setEid(e.target.value)}
                     placeholder="e.g. PO-00001" className="mono" />
            </Field>
          </div>
          <button className="btn primary" style={{ marginBottom: 14 }} onClick={load} disabled={!eid || busy}>
            {busy ? <span className="spin" /> : "🔍"} Trace workflow
          </button>
          <div className="spacer" />
          <div className="small muted" style={{ paddingBottom: 18 }}>
            Try: PO-00001 · SO-00001 · MPO-00001 · AP-00001
          </div>
        </div>
      </div>

      {err && <div className="error-box mb">{err}</div>}

      {data && (
        <>
          <div className="card mb rise d1">
            <h3>{etype} {eid} — lifecycle</h3>
            <div className="card-sub">Green = done · Blue = current · Grey = pending · next legal steps: {(data.allowed_next || []).join(", ") || "—"}</div>
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

          <div className="card rise d2">
            <h3>Audit trail ({audit.length} events)</h3>
            <div className="card-sub">Append-only — every state change, actor and reason</div>
            {audit.length === 0 ? (
              <div className="empty">No audit events.</div>
            ) : (
              <Timeline items={audit.map((a) => ({
                title: `${a.action?.replace(/_/g, " ")} — ${a.actor?.type === "AGENT" ? "🤖" : "👤"} ${a.actor?.id || "system"}`,
                detail: `${a.previous_state || "—"} → ${a.new_state || "—"}${a.reason ? ` · ${a.reason}` : a.details?.reason ? ` · ${a.details.reason}` : ""}`,
                meta: `${When(a.timestamp)}${a.correlation_id ? ` · corr ${a.correlation_id}` : ""}`,
                tone: a.action === "REJECTED" ? "red" : a.action === "APPROVED" ? "green" : "",
              }))} />
            )}
          </div>
        </>
      )}
    </div>
  );
}
