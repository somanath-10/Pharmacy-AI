import React, { useEffect, useState } from "react";
import { BrowserRouter, Routes, Route, NavLink, Navigate } from "react-router-dom";
import { api } from "./api";
import { ToastHost } from "./ui";
import Login from "./pages/Login";
import CommandCenter from "./pages/CommandCenter";
import SalesWorkspace from "./pages/SalesWorkspace";
import SupplyWorkspace from "./pages/SupplyWorkspace";
import VendorsWorkspace from "./pages/VendorsWorkspace";
import WarehouseWorkspace from "./pages/WarehouseWorkspace";
import QualityWorkspace from "./pages/QualityWorkspace";
import PlantWorkspace from "./pages/PlantWorkspace";
import PharmacyWorkspace from "./pages/PharmacyWorkspace";
import LogisticsWorkspace from "./pages/LogisticsWorkspace";
import FinanceWorkspace from "./pages/FinanceWorkspace";
import DecisionQueue from "./pages/DecisionQueue";
import WorkflowViewer from "./pages/WorkflowViewer";

const NAV = [
  { section: "AI Operations" },
  { to: "/", label: "AI Command Center", ico: "🧠", end: true },
  { to: "/queue", label: "Human Decision Queue", ico: "🙋", pill: "human" },
  { section: "Workspaces" },
  { to: "/sales", label: "Customers & Sales", ico: "📈" },
  { to: "/supply", label: "Supply Chain", ico: "📆" },
  { to: "/vendors", label: "Vendors & Procurement", ico: "🛒" },
  { to: "/warehouse", label: "Warehouse & Inventory", ico: "📦" },
  { to: "/quality", label: "Quality (QA/QC)", ico: "🧪" },
  { to: "/plant", label: "Plant / Production", ico: "🏭" },
  { to: "/pharmacy", label: "Pharmacy", ico: "💊" },
  { to: "/logistics", label: "Logistics", ico: "🚚" },
  { to: "/finance", label: "Finance & Governance", ico: "💰" },
  { section: "Governance" },
  { to: "/workflows", label: "Workflow Viewer", ico: "🔍" },
];

function Shell({ children }) {
  const me = getMe();
  const [pending, setPending] = useState(0);

  useEffect(() => onAuthChange(() => setPending(0)), []);
  useEffect(() => {
    let stop = false;
    const tick = async () => {
      try {
        const s = await api("/api/approvals?status=PENDING");
        const n = Array.isArray(s) ? s.length : (s.items ? s.items.length : s.count || 0);
        if (!stop) setPending(n);
      } catch { /* silent */ }
    };
    tick();
    const t = setInterval(tick, 15000);
    return () => { stop = true; clearInterval(t); };
  }, []);

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="logo">
          <div className="logo-badge">✦</div>
          <div>
            <b>Pharma AI OS</b>
            <small>Autonomous Enterprise Core</small>
          </div>
        </div>
        {NAV.map((n, i) =>
          n.section ? (
            <div className="nav-section" key={`s${i}`}>{n.section.toUpperCase()}</div>
          ) : (
            <NavLink key={n.to} to={n.to} end={n.end}
                     className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}>
              <span className="ico">{n.ico}</span>
              {n.label}
              {n.pill === "human" && pending > 0 && <span className="pill">{pending}</span>}
            </NavLink>
          )
        )}
        <div style={{ marginTop: 22, padding: "0 8px" }}>
          <button className="btn ghost sm" style={{ width: "100%" }} onClick={logout}>
            Sign out
          </button>
        </div>
      </aside>
      <main className="main">{children}</main>
    </div>
  );
}

function Topbar({ title, sub }) {
  const me = getMe();
  return (
    <div className="topbar">
      <div>
        <h1>{title}</h1>
        {sub && <div className="sub">{sub}</div>}
      </div>
      <div className="spacer" />
      <span className="live-chip"><span className="dot green pulse" /> Live — agents orchestrating</span>
      <div className="user-chip">
        <div className="avatar">{(me?.email || "U").slice(0, 2).toUpperCase()}</div>
        <div>
          <b>{me?.email?.split("@")[0] || "user"}</b>
          <small>{(me?.roles || []).join(", ") || "user"}</small>
        </div>
      </div>
    </div>
  );
}
export { Topbar };

export default function App() {
  const [authed, setAuthed] = useState(!!getToken());
  useEffect(() => onAuthChange(() => setAuthed(!!getToken())), []);

  return (
    <ToastHost>
      <BrowserRouter>
        {!authed ? (
          <Routes>
            <Route path="*" element={<Login />} />
          </Routes>
        ) : (
          <Shell>
            <Routes>
              <Route path="/" element={<CommandCenter />} />
              <Route path="/queue" element={<DecisionQueue />} />
              <Route path="/sales" element={<SalesWorkspace />} />
              <Route path="/supply" element={<SupplyWorkspace />} />
              <Route path="/vendors" element={<VendorsWorkspace />} />
              <Route path="/warehouse" element={<WarehouseWorkspace />} />
              <Route path="/quality" element={<QualityWorkspace />} />
              <Route path="/plant" element={<PlantWorkspace />} />
              <Route path="/pharmacy" element={<PharmacyWorkspace />} />
              <Route path="/logistics" element={<LogisticsWorkspace />} />
              <Route path="/finance" element={<FinanceWorkspace />} />
              <Route path="/workflows" element={<WorkflowViewer />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </Shell>
        )}
      </BrowserRouter>
    </ToastHost>
  );
}
