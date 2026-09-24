import React, { useEffect, useState } from "react";
import { api } from "../api";
import { Topbar } from "../App";
import { Badge, Table, When, Kpi, CountTabs, Empty } from "../ui";

export default function AgentActivity() {
  const [data, setData] = useState(null);
  const [tab, setTab] = useState("Tool calls");
  const [err, setErr] = useState("");

  const load = async () => {
    try {
      setData(await api("/api/analytics/agent-activity"));
      setErr("");
    } catch (e) { setErr(e.message); }
  };
  useEffect(() => { load(); const t = setInterval(load, 12000); return () => clearInterval(t); }, []);

  const s = data?.summary_24h || {};
  const calls = data?.tool_calls || [];
  const runs = data?.supervisor_runs || [];

  return (
    <div>
      <Topbar title="Agent Activity"
              sub="What the AI is actually doing — every governed tool call with result, duration, confidence and escalations" />
      {err && <div className="error-box mb">{err}</div>}
      {!data && !err && <div className="card"><div className="skeleton" style={{ height: 200 }} /></div>}

      {data && (
        <>
          <div className="grid kpi-4 mb">
            <Kpi ico="✅" label="Successful calls (24h)" value={s.success ?? "—"} tone="green" />
            <Kpi ico="❌" label="Failed calls (24h)" value={s.failed ?? "—"} tone={s.failed ? "red" : "green"} />
            <Kpi ico="🙋" label="Escalated to humans" value={s.escalated ?? "—"} tone={s.escalated ? "orange" : "green"} />
            <Kpi ico="⚡" label="Median duration" value={s.p50_ms != null ? `${s.p50_ms} ms` : "—"} tone="blue" />
          </div>

          <CountTabs tabs={[{ label: "Tool calls", count: calls.length },
                            { label: "Supervisor runs", count: runs.length }]}
                     active={tab} onChange={setTab} />
          <div className="mt" />

          {tab === "Tool calls" && (
            <div className="card">
              <h3>Governed tool calls</h3>
              <div className="card-sub">Agent → Tool Gateway → Permission → Policy → Domain API → Event → Audit. Agents never write MongoDB directly.</div>
              {calls.length === 0
                ? <Empty art="🤖" title="No agent activity yet" note="Run an agent from any workspace." />
                : <Table rows={calls} empty="No agent activity"
                    columns={[
                      { key: "at", label: "When", render: (r) => <span className="small">{When(r.created_at)}</span> },
                      { key: "agent", label: "Agent", render: (r) => <b>{r.agent}</b> },
                      { key: "tool", label: "Tool", render: (r) => <span className="mono">{r.tool}</span> },
                      { key: "reason", label: "Reason", render: (r) => <span className="small muted">{r.reason || "—"}</span> },
                      { key: "conf", label: "Confidence", render: (r) => r.confidence != null
                          ? <span className="small">{Math.round(r.confidence * 100) / 100}</span>
                          : <span className="muted">—</span> },
                      { key: "dur", label: "Took", render: (r) => r.duration_ms != null
                          ? <span className="small">{r.duration_ms} ms</span> : <span className="muted">—</span> },
                      { key: "status", label: "Result", render: (r) => <Badge>{r.status}</Badge> },
                      { key: "err", label: "Error / escalation", render: (r) => (
                        <span className="small text-red">{r.error || r.escalation_reason || ""}</span>) },
                    ]} />}
            </div>
          )}

          {tab === "Supervisor runs" && (
            <div className="card">
              <h3>Supervisor tick runs</h3>
              <div className="card-sub">Self-resolution sweeps: expiring approvals, licence checks, shortage scans</div>
              {runs.length === 0
                ? <Empty art="🫂" title="No supervisor runs yet" />
                : <Table rows={runs} empty="No runs"
                    columns={[
                      { key: "at", label: "When", render: (r) => <span className="small">{When(r.created_at)}</span> },
                      { key: "agent", label: "Agent", render: (r) => <b>{r.agent}</b> },
                      { key: "type", label: "Type", render: (r) => <Badge>{r.type}</Badge> },
                      { key: "actions", label: "Actions", render: (r) => (r.results?.actions || []).length },
                    ]} />}
            </div>
          )}
        </>
      )}
    </div>
  );
}
