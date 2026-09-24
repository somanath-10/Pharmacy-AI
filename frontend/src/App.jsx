import React, { useEffect, useState } from "react";
import { BrowserRouter, Routes, Route, NavLink, Navigate } from "react-router-dom";
import { api, getToken, getMe, onAuthChange, logout } from "./api";
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
import AgentActivity from "./pages/AgentActivity";
import ExceptionCenter from "./pages/ExceptionCenter";
import AdminPage from "./pages/AdminPage";
import ExecutiveDashboard from "./pages/ExecutiveDashboard";

// Role-aware navigation: users only see modules their roles permit.
// Sections follow the blueprint main-menu order (Part 4). Backend
// authorization remains authoritative — this only declutters the UI.
const NAV_ITEMS = [
  { to: "/", label: "AI Command Center", ico: "🧠", end: true, roles: "any", section: "Overview" },
  { to: "/executive", label: "Executive Dashboard", ico: "📊",
    roles: ["SUPER_ADMIN", "MANAGEMENT"], section: "Overview" },
  { to: "/queue", label: "Human Decision Queue", ico: "🙋", pill: "human",
    roles: ["SUPER_ADMIN", "MANAGEMENT", "FINANCE", "QA", "VENDOR_MANAGER", "PHARMACIST", "COMPLIANCE", "PLANT", "WAREHOUSE"],
    section: "Overview" },
  { to: "/exceptions", label: "Exception Center", ico: "⚠️",
    roles: ["SUPER_ADMIN", "MANAGEMENT", "FINANCE", "QA", "COMPLIANCE", "PLANNING", "LOGISTICS", "WAREHOUSE", "AUDITOR"],
    section: "Overview" },

  { to: "/sales", label: "Customers / CRM / Sales", ico: "📈",
    roles: ["SUPER_ADMIN", "SALES", "MANAGEMENT", "LOGISTICS", "FINANCE"], section: "Commercial" },
  { to: "/supply", label: "Supply Chain Planning", ico: "📆",
    roles: ["SUPER_ADMIN", "PLANNING", "MANAGEMENT", "PROCUREMENT", "BUYER", "SALES", "WAREHOUSE"], section: "Commercial" },

  { to: "/vendors", label: "Vendors / Sourcing / Procurement", ico: "🛒",
    roles: ["SUPER_ADMIN", "PROCUREMENT", "BUYER", "VENDOR_MANAGER", "FINANCE", "QA", "MANAGEMENT", "SUPPLIER"], section: "Supply" },

  { to: "/logistics", label: "Logistics (In/Outbound)", ico: "🚚",
    roles: ["SUPER_ADMIN", "LOGISTICS", "WAREHOUSE", "SALES", "MANAGEMENT", "SUPPLIER"], section: "Operations" },
  { to: "/warehouse", label: "Warehouse / WMS / Inventory", ico: "📦",
    roles: ["SUPER_ADMIN", "WAREHOUSE", "LOGISTICS", "MANAGEMENT", "QC", "PLANNING"], section: "Operations" },
  { to: "/quality", label: "QC / LIMS · QA / QMS", ico: "🧪",
    roles: ["SUPER_ADMIN", "QA", "QC", "MANAGEMENT", "COMPLIANCE", "PLANT"], section: "Operations" },

  { to: "/plant", label: "Plant / Production", ico: "🏭",
    roles: ["SUPER_ADMIN", "PLANT", "QA", "MANAGEMENT", "PLANNING"], section: "Production & Care" },
  { to: "/pharmacy", label: "Pharmacy / POS", ico: "💊",
    roles: ["SUPER_ADMIN", "PHARMACIST", "SALES", "MANAGEMENT"], section: "Production & Care" },
  { to: "/finance", label: "Finance · Returns · Recall · PV", ico: "💰",
    roles: ["SUPER_ADMIN", "FINANCE", "MANAGEMENT", "AUDITOR", "COMPLIANCE", "QA"], section: "Production & Care" },

  { to: "/agents", label: "Agent Activity", ico: "🤖", roles: "any", section: "Governance" },
  { to: "/workflows", label: "Workflow Viewer / Audit", ico: "🔍", roles: "any", section: "Governance" },
  { to: "/admin", label: "Master Data & Administration", ico: "⚙️",
    roles: ["SUPER_ADMIN", "MANAGEMENT", "COMPLIANCE"], section: "Governance" },
];

function visibleNav(me) {
  const roles = me?.roles || [];
  const isExternal = roles.includes("SUPPLIER") || roles.includes("CUSTOMER");
  return NAV_ITEMS.filter((n) => {
    if (n.roles === "any") return !isExternal;
    if (!Array.isArray(n.roles)) return true;
    return n.roles.some((r) => roles.includes(r));
  });
}

function navWithSections(me) {
  const out = [];
  let last = null;
  for (const n of visibleNav(me)) {
    if (n.section !== last) { out.push({ section: n.section }); last = n.section; }
    out.push(n);
  }
  return out;
}

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
        {navWithSections(me).map((n, i) =>
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
        <div className="sidebar-foot">
          <button className="btn ghost sm" style={{ width: "100%", marginBottom: 8 }} onClick={logout}>
            ⎋ Sign out
          </button>
          <div>v2 · {visibleNav(me).length} modules visible to your roles</div>
        </div>
      </aside>
      <main className="main">{children}</main>
    </div>
  );
}

export function Topbar({ title, sub }) {
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
              <Route path="/agents" element={<AgentActivity />} />
              <Route path="/executive" element={<ExecutiveDashboard />} />
              <Route path="/exceptions" element={<ExceptionCenter />} />
              <Route path="/admin" element={<AdminPage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </Shell>
        )}
      </BrowserRouter>
    </ToastHost>
  );
}
