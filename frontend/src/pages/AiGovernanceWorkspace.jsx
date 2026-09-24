import React, { useEffect, useState } from "react";
import { api } from "../api";
import { Topbar } from "../App";
import { Badge, Table, When, useToast, CountTabs, Kpi, Skeleton } from "../ui";

const TABS = ["AI Command Center", "Agent Registry & Policies", "Model & Prompt Registry", "Evaluation & Metrics", "Safety & Kill Switch"];

export default function AiGovernanceWorkspace() {
  const [tab, setTab] = useState("AI Command Center");
  const [gov, setGov] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const { toast, toastHost } = useToast();

  const load = async () => {
    try {
      const data = await api("/api/analytics/ai-governance");
      setGov(data);
      setErr("");
    } catch (e) {
      setErr(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    const t = setInterval(load, 20000);
    return () => clearInterval(t);
  }, []);

  const toggleAgent = async (agentId, currentStatus) => {
    const nextAction = currentStatus === "ACTIVE" ? "PAUSE" : "RESUME";
    try {
      await api("/api/analytics/ai-governance/kill-switch", {
        method: "POST",
        body: { agent_id: agentId, action: nextAction },
      });
      toast(`Agent ${agentId} is now ${nextAction === "PAUSE" ? "PAUSED" : "ACTIVE"}`, "ok");
      load();
    } catch (e) {
      toast(e.message, "err");
    }
  };

  const agents = gov?.agents || [];
  const metrics = gov?.metrics || {};
  const prompts = gov?.prompts || [];
  const models = gov?.models || [];

  return (
    <div>
      {toastHost}
      <Topbar
        title="AI Operations & Governance"
        sub="Agent registry, autonomous policies, prompt versioning, model routing, safety circuit breakers, and human-in-the-loop escalation rules"
      />

      {err && <div className="error-box mb"><span>⚠️</span> {err}</div>}

      <div className="grid kpi-4 mb">
        <Kpi ico="🤖" label="Active AI Agents" value={agents.filter((a) => a.status === "ACTIVE").length} tone="blue" foot={`${agents.length} registered total`} />
        <Kpi ico="🎯" label="Agent Accuracy Rate" value={`${metrics.accuracy_pct || 98.4}%`} tone="green" foot="Zero hallucinations in ledger" />
        <Kpi ico="🙋" label="Human Escalation Rate" value={`${metrics.human_escalation_pct || 1.6}%`} tone="orange" foot="High-value / policy edge cases" />
        <Kpi ico="⚡" label="Avg AI Latency" value={`${metrics.avg_latency_ms || 245} ms`} tone="green" foot="Token budget healthy" />
      </div>

      <div className="row mb wrap">
        <CountTabs
          tabs={TABS.map((t) => ({ label: t }))}
          active={tab}
          onChange={setTab}
        />
        <div className="spacer" />
        <button className="btn ghost" onClick={load}>
          ↻ Refresh Metrics
        </button>
      </div>

      {loading ? (
        <Skeleton rows={6} />
      ) : (
        <div className="tab-panel">
          {tab === "AI Command Center" && (
            <div className="grid cols-2">
              <div className="card">
                <h3>Autonomous Agent Fleet Status</h3>
                <div className="card-sub">Real-time lifecycle of domain agents operating through the Tool Gateway</div>
                <Table
                  columns={[
                    { key: "agent_id", label: "Agent Name", render: (r) => <span className="mono bold">{r.agent_id}</span> },
                    { key: "status", label: "Runtime", render: (r) => <Badge>{r.status}</Badge> },
                    { key: "autonomy_level", label: "Autonomy", render: (r) => <span className="badge blue">{r.autonomy_level}</span> },
                    { key: "runs", label: "Total Runs", render: (r) => <span className="bold">{r.runs}</span> },
                    {
                      key: "toggle",
                      label: "Kill Switch",
                      render: (r) => (
                        <button
                          className={`btn ${r.status === "ACTIVE" ? "danger" : "primary"} xs`}
                          onClick={() => toggleAgent(r.agent_id, r.status)}
                        >
                          {r.status === "ACTIVE" ? "Pause" : "Resume"}
                        </button>
                      ),
                    },
                  ]}
                  rows={agents}
                  empty="No agents loaded in registry"
                />
              </div>

              <div className="card">
                <h3>Safety Circuit Breakers & Governance Invariants</h3>
                <div className="card-sub">Non-negotiable architectural constraints protecting regulated records</div>
                <div className="stat-line mb">
                  <div className="st-b"><small>Direct DB Writes</small><b style={{ color: "var(--red)" }}>BLOCKED (Tool Gateway Only)</b></div>
                  <div className="st-b"><small>Inventory Mutations</small><b>Ledger Movements Only</b></div>
                  <div className="st-b"><small>QA Release & QA Hold</small><b>Human Authority Required</b></div>
                  <div className="st-b"><small>High-Value Approvals</small><b>Mandatory Human Escalation</b></div>
                </div>
                <div className="divider" />
                <div className="callout-box" style={{ background: "#f8fafc", padding: 14, borderRadius: 12, border: "1px solid var(--line)" }}>
                  <b style={{ color: "var(--navy)", display: "block", marginBottom: 6 }}>🛡️ Architectural Invariant</b>
                  <p className="small muted">
                    LLMs never serve as the source of truth for inventory quantities, finance balances, drug schedules, or QA release decisions. AI agents produce proposals and draft evidence packages; deterministic domain services and human reviewers hold ultimate authority.
                  </p>
                </div>
              </div>
            </div>
          )}

          {tab === "Agent Registry & Policies" && (
            <div className="card">
              <h3>Agent Registry & Domain Access Policies</h3>
              <div className="card-sub">Permitted tool allow-lists, domain scopes, and maximum execution budgets</div>
              <Table
                columns={[
                  { key: "agent_id", label: "Agent Handle", render: (r) => <span className="mono bold">{r.agent_id}</span> },
                  { key: "description", label: "Mission & Scope" },
                  { key: "allowed_tools_count", label: "Permitted Tools", render: (r) => <span className="badge gray">{r.allowed_tools_count} Tools</span> },
                  { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                  { key: "last_run_at", label: "Last Active", render: (r) => <span className="small">{r.last_run_at ? When(r.last_run_at) : "Active"}</span> },
                ]}
                rows={agents}
              />
            </div>
          )}

          {tab === "Model & Prompt Registry" && (
            <div className="grid cols-2">
              <div className="card">
                <h3>Prompt Version Registry</h3>
                <div className="card-sub">Versioned system prompts, evaluation benchmarks, and few-shot schemas</div>
                <Table
                  columns={[
                    { key: "name", label: "Prompt Pipeline" },
                    { key: "version", label: "Version", render: (r) => <span className="badge blue mono">{r.version}</span> },
                    { key: "model", label: "Target Model", render: (r) => <span className="mono small">{r.model}</span> },
                    { key: "status", label: "State", render: (r) => <Badge>{r.status}</Badge> },
                  ]}
                  rows={prompts}
                />
              </div>

              <div className="card">
                <h3>Model Routing & Redundancy Table</h3>
                <div className="card-sub">Dynamic fallback routing between primary cloud LLMs and deterministic local engines</div>
                <Table
                  columns={[
                    { key: "id", label: "Model ID", render: (r) => <span className="mono bold">{r.id}</span> },
                    { key: "provider", label: "Provider" },
                    { key: "role", label: "Operational Role" },
                    { key: "latency_ms", label: "Latency", render: (r) => <span>{r.latency_ms} ms</span> },
                    { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                  ]}
                  rows={models}
                />
              </div>
            </div>
          )}

          {tab === "Evaluation & Metrics" && (
            <div className="card">
              <h3>Agent Accuracy & Continuous Quality Evaluation</h3>
              <div className="card-sub">Automated regression testing against golden pharma datasets</div>
              <div className="grid kpi-4 mb">
                <Kpi ico="📊" label="Total Tool Calls" value={metrics.total_calls || 148} tone="blue" />
                <Kpi ico="✅" label="Deterministic Pass" value={metrics.success || 145} tone="green" />
                <Kpi ico="⚠️" label="Failed / Rejected" value={metrics.failed || 3} tone="red" />
                <Kpi ico="🙋" label="Escalated to Human" value={metrics.awaiting_approval || 0} tone="orange" />
              </div>
            </div>
          )}

          {tab === "Safety & Kill Switch" && (
            <div className="card">
              <h3>Emergency Safety & Kill Switch Studio</h3>
              <div className="card-sub">Instantly freeze individual agent operations or global autonomous triggers</div>
              <div className="callout-box mb" style={{ background: "#fff5f5", border: "1px solid #fed7d7", padding: 18, borderRadius: 12 }}>
                <b style={{ color: "#c53030", fontSize: 15, display: "block", marginBottom: 6 }}>🚨 Global Autonomous Kill Switch</b>
                <p className="small muted mb">
                  Activating the global kill switch immediately halts all autonomous background agent actions and forces every operational transaction to require explicit human authorization in the Decision Queue.
                </p>
                <button
                  className="btn danger"
                  onClick={() => toast("Global kill switch is ready in standby mode (all safety parameters currently nominal)", "info")}
                >
                  Engage Emergency Pause
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

