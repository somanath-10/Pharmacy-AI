import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { Topbar } from "../App";
import { Badge, Table, When, Kpi, CountTabs, Empty } from "../ui";

const TYPE_ROUTE = {
  INVOICE_MISMATCH: "/finance", QC_OOS: "/quality",
  SHIPMENT_EXCEPTION: "/logistics", QUALITY_HOLD: "/quality",
  VENDOR_ISSUE: "/vendors", PAYMENT_FAILED: "/finance",
};

export default function ExceptionCenter() {
  const nav = useNavigate();
  const [data, setData] = useState(null);
  const [tab, setTab] = useState("Unresolved");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const load = async (auto = true) => {
    setBusy(true);
    try {
      setData(await api(`/api/analytics/exception-center?auto=${auto}`));
      setErr("");
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };
  useEffect(() => { load(); const t = setInterval(() => load(), 20000); return () => clearInterval(t); }, []);

  const unresolved = data?.unresolved || [];
  const resolved = data?.auto_resolved || [];

  return (
    <div>
      <Topbar title="Exception Center"
              sub="Every unresolved problem in one place — agents self-resolve first; humans only see what automation could not fix" />
      {err && <div className="error-box mb">{err}</div>}
      {!data && !err && <div className="card"><div className="skeleton" style={{ height: 200 }} /></div>}

      {data && (
        <>
          <div className="grid kpi-4 mb">
            <Kpi ico="⚠️" label="Unresolved" value={unresolved.length}
                 tone={unresolved.length ? "orange" : "green"}
                 foot="routed to agents, then humans" />
            <Kpi ico="🤖" label="Auto-resolved" value={resolved.length} tone="green"
                 foot="fixed by self-resolution loop" />
            <Kpi ico="🚨" label="High severity" value={unresolved.filter((p) => p.severity === "HIGH").length}
                 tone={unresolved.some((p) => p.severity === "HIGH") ? "red" : "green"} />
            <Kpi ico="🕐" label="Checked at" value={<span className="small">{When(data.checked_at)}</span>} />
          </div>

          <div className="row mb">
            <CountTabs tabs={[{ label: "Unresolved", count: unresolved.length },
                              { label: "Auto-resolved", count: resolved.length }]}
                       active={tab} onChange={setTab} />
            <div className="spacer" />
            <button className="btn primary sm" disabled={busy}
                    onClick={() => load(true)}>
              {busy ? <span className="spin" /> : "🔄"} Re-run self-resolution
            </button>
          </div>

          <div className="card">
            {tab === "Unresolved" ? (
              <>
                <h3>Needs human or further agent action</h3>
                <div className="card-sub">These have already passed through the agent self-resolution loop without success.</div>
                {unresolved.length === 0
                  ? <Empty art="✨" title="Nothing unresolved" note="All exceptions cleared." />
                  : <Table rows={unresolved} empty="Nothing unresolved"
                      columns={[
                        { key: "type", label: "Problem", render: (r) => <Badge>{r.type}</Badge> },
                        { key: "entity", label: "Record", render: (r) => <span className="mono">{r.entity_id}</span> },
                        { key: "detail", label: "Detail", render: (r) => <span className="small">{r.detail}</span> },
                        { key: "sev", label: "Severity", render: (r) => (
                          <span className={`small ${r.severity === "HIGH" ? "text-red" : "text-orange"}`}
                                style={{ fontWeight: 700 }}>{r.severity}</span>) },
                        { key: "auto", label: "Agent attempt", render: (r) => (
                          <span className="small muted">
                            {r.auto_resolution
                              ? (r.auto_resolution.trail || [r.auto_resolution.route || "no automated resolution"]).join(" → ")
                              : "—"}
                          </span>) },
                        { key: "go", label: "", render: (r) => (
                          <button className="btn ghost sm"
                                  onClick={() => nav(TYPE_ROUTE[r.type] || "/queue")}>
                            Open module →
                          </button>) },
                      ]} />}
              </>
            ) : (
              <>
                <h3>Resolved without human touch</h3>
                <div className="card-sub">Proof of automation: problems fixed by the supervisor loop.</div>
                {resolved.length === 0
                  ? <Empty art="🫧" title="Nothing auto-resolved in this pass" />
                  : (
                      <>
                        <Table rows={resolved} empty="—"
                            columns={[
                              { key: "type", label: "Problem", render: (r) => <Badge>{r.type}</Badge> },
                              { key: "entity", label: "Record", render: (r) => <span className="mono">{r.entity_id}</span> },
                              { key: "detail", label: "Detail", render: (r) => <span className="small">{r.detail}</span> },
                              { key: "how", label: "Resolution", render: (r) => (
                                <span className="small muted">
                                  {(r.auto_resolution?.trail || []).join(" → ") || "resolved"}
                                </span>) },
                            ]} />
                      </>
                    )}
              </>
            )}
          </div>
        </>
      )}
    </div>
  );
}