import React, { useEffect, useState } from "react";
import { api } from "../api";
import { Topbar } from "../App";
import { Badge, Table, When, Kpi, Empty } from "../ui";

export default function AdminPage() {
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");

  const load = async () => {
    try { setData(await api("/api/analytics/admin/overview")); setErr(""); }
    catch (e) { setErr(e.message); }
  };
  useEffect(() => { load(); }, []);

  return (
    <div>
      <Topbar title="Master Data & Administration"
              sub="Users, roles, agent governance and system reference data" />
      {err && <div className="error-box mb">Admin overview requires elevated roles — {err}</div>}
      {!data && !err && <div className="card"><div className="skeleton" style={{ height: 200 }} /></div>}

      {data && (
        <>
          <div className="grid kpi-4 mb">
            <Kpi ico="👤" label="Users" value={data.user_count} tone="blue" />
            <Kpi ico="🎭" label="Roles in use" value={(data.roles_in_use || []).length} tone="purple"
                 foot={(data.roles_in_use || []).slice(0, 4).join(", ")} />
            <Kpi ico="🤖" label="Agents active" value={(data.agents || []).length} tone="teal" />
            <Kpi ico="📜" label="Licences tracked" value={data.open_licences_tracked} tone="orange" />
          </div>

          <div className="grid cols-2">
            <div className="card">
              <h3>Users & role assignments</h3>
              <div className="card-sub">Role changes are audited; backend authorization is authoritative</div>
              {data.users.length === 0
                ? <Empty art="👤" title="No users visible" />
                : <Table rows={data.users} empty="—"
                    columns={[
                      { key: "email", label: "User", render: (r) => <span className="mono">{r.email}</span> },
                      { key: "roles", label: "Roles", render: (r) => (
                        <span className="small">{(r.roles || []).join(", ")}</span>) },
                      { key: "mfa", label: "MFA", render: (r) => r.mfa
                          ? <Badge>ON</Badge> : <span className="muted small">off</span> },
                      { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                    ]} />}
            </div>
            <div className="card">
              <h3>Agent governance</h3>
              <div className="card-sub">Lifetime tool-call volume and error counts per agent</div>
              {(data.agents || []).length === 0
                ? <Empty art="🤖" title="No agent calls recorded" />
                : <Table rows={data.agents} empty="—"
                    columns={[
                      { key: "agent", label: "Agent", render: (r) => <b>{r._id}</b> },
                      { key: "calls", label: "Calls" },
                      { key: "errors", label: "Errors", render: (r) => (
                        <span className={r.errors ? "text-red" : ""}>{r.errors}</span>) },
                    ]} />}
            </div>
          </div>

          <div className="card mt">
            <h3>Master data</h3>
            <div className="card-sub">Products, price lists, specifications and equipment are managed in their domain workspaces</div>
            <div className="row wrap gap">
              <a className="link" href="/supply">Supply & products →</a>
              <a className="link" href="/vendors">Vendors & contracts →</a>
              <a className="link" href="/quality">Specifications & equipment →</a>
              <a className="link" href="/workflows">Audit & workflows →</a>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
