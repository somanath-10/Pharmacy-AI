import React, { useEffect, useState } from "react";
import { BrowserRouter, Routes, Route, NavLink, Navigate, useNavigate } from "react-router-dom";
import { api, getToken, getMe, setAuth, getRefresh, onAuthChange, logout } from "./api";
import { ToastHost } from "./ui";

import Login from "./pages/Login";
import CommandCenter from "./pages/CommandCenter";
import ExecutiveDashboard from "./pages/ExecutiveDashboard";
import DecisionQueue from "./pages/DecisionQueue";
import ExceptionCenter from "./pages/ExceptionCenter";
import SalesWorkspace from "./pages/SalesWorkspace";
import SupplyWorkspace from "./pages/SupplyWorkspace";
import VendorsWorkspace from "./pages/VendorsWorkspace";
import WarehouseWorkspace from "./pages/WarehouseWorkspace";
import QualityWorkspace from "./pages/QualityWorkspace";
import PlantWorkspace from "./pages/PlantWorkspace";
import EngineeringWorkspace from "./pages/EngineeringWorkspace";
import PharmacyWorkspace from "./pages/PharmacyWorkspace";
import LogisticsWorkspace from "./pages/LogisticsWorkspace";
import FinanceWorkspace from "./pages/FinanceWorkspace";
import ComplianceWorkspace from "./pages/ComplianceWorkspace";
import SafetyWorkspace from "./pages/SafetyWorkspace";
import AuditWorkspace from "./pages/AuditWorkspace";
import AiGovernanceWorkspace from "./pages/AiGovernanceWorkspace";
import MasterDataWorkspace from "./pages/MasterDataWorkspace";
import PortalsWorkspace from "./pages/PortalsWorkspace";
import SopTraining from "./pages/SopTraining";
import DocumentCenter from "./pages/DocumentCenter";
import UserProfile from "./pages/UserProfile";
import WorkflowViewer from "./pages/WorkflowViewer";
import AgentActivity from "./pages/AgentActivity";
import AdminPage from "./pages/AdminPage";
import GlobalSearchModal from "./pages/GlobalSearchModal";

// ── Role Default Landing Workspace Resolution ──
export function getRoleHome(roles = []) {
  if (!roles || !roles.length) return "/queue";
  if (roles.includes("SUPER_ADMIN") || roles.includes("MANAGEMENT")) return "/";
  if (roles.includes("PHARMACIST")) return "/pharmacy";
  if (roles.includes("WAREHOUSE")) return "/warehouse";
  if (roles.includes("PLANT")) return "/plant";
  if (roles.includes("QC") || roles.includes("QA")) return "/quality";
  if (roles.includes("PROCUREMENT") || roles.includes("BUYER") || roles.includes("VENDOR_MANAGER")) return "/vendors";
  if (roles.includes("SALES")) return "/sales";
  if (roles.includes("FINANCE")) return "/finance";
  if (roles.includes("LOGISTICS")) return "/logistics";
  if (roles.includes("COMPLIANCE")) return "/compliance";
  if (roles.includes("AUDITOR")) return "/audit";
  if (roles.includes("SUPPLIER") || roles.includes("CUSTOMER")) return "/portals";
  return "/queue";
}

// ── Enterprise Role Navigation Matrix ──
const NAV_ITEMS = [
  // ── Enterprise Operations ──
  { to: "/", label: "AI Command Center", end: true, roles: ["SUPER_ADMIN", "MANAGEMENT"], section: "Enterprise Operations" },
  { to: "/executive", label: "Executive Control Tower", roles: ["SUPER_ADMIN", "MANAGEMENT"], section: "Enterprise Operations" },
  { to: "/supply", label: "Supply Chain Planning", roles: ["SUPER_ADMIN", "PLANNING", "MANAGEMENT", "PROCUREMENT", "BUYER", "SALES", "WAREHOUSE"], section: "Enterprise Operations" },
  { to: "/sales", label: "Sales & CRM", roles: ["SUPER_ADMIN", "SALES", "MANAGEMENT", "LOGISTICS", "FINANCE"], section: "Enterprise Operations" },
  { to: "/vendors", label: "Procurement & Sourcing", roles: ["SUPER_ADMIN", "PROCUREMENT", "BUYER", "VENDOR_MANAGER", "FINANCE", "QA", "MANAGEMENT"], section: "Enterprise Operations" },
  { to: "/warehouse", label: "Warehouse & WMS", roles: ["SUPER_ADMIN", "WAREHOUSE", "LOGISTICS", "MANAGEMENT", "QC", "PLANNING"], section: "Enterprise Operations" },
  { to: "/quality", label: "QC / LIMS · QA / QMS", roles: ["SUPER_ADMIN", "QA", "QC", "MANAGEMENT", "COMPLIANCE", "PLANT"], section: "Enterprise Operations" },
  { to: "/plant", label: "Plant & Production", roles: ["SUPER_ADMIN", "PLANT", "QA", "MANAGEMENT", "PLANNING"], section: "Enterprise Operations" },
  { to: "/engineering", label: "Engineering & Maintenance", roles: ["SUPER_ADMIN", "PLANT", "QC", "MANAGEMENT"], section: "Enterprise Operations" },
  { to: "/pharmacy", label: "Pharmacy & POS Counter", roles: ["SUPER_ADMIN", "PHARMACIST", "SALES", "MANAGEMENT"], section: "Enterprise Operations" },
  { to: "/logistics", label: "Logistics & Fleet", roles: ["SUPER_ADMIN", "LOGISTICS", "WAREHOUSE", "SALES", "MANAGEMENT"], section: "Enterprise Operations" },
  { to: "/finance", label: "Finance & Accounts", roles: ["SUPER_ADMIN", "FINANCE", "MANAGEMENT", "AUDITOR", "COMPLIANCE"], section: "Enterprise Operations" },

  // ── Common Suite (Internal Roles) ──
  { to: "/queue", label: "Human Decision Queue", pill: "human", roles: "any", section: "Common Suite" },
  { to: "/exceptions", label: "Exception Center", roles: "any", section: "Common Suite" },
  { to: "/documents", label: "Document Center & OCR", roles: "any", section: "Common Suite" },
  { to: "/workflows", label: "Workflow Timeline", roles: "any", section: "Common Suite" },
  { to: "/agents", label: "Agent Activity Feed", roles: "any", section: "Common Suite" },
  { to: "/sop-training", label: "SOP & Training Matrix", roles: "any", section: "Common Suite" },
  { to: "/profile", label: "My Profile & Security", roles: "any", section: "Common Suite" },

  // ── Governance & Regulation ──
  { to: "/compliance", label: "Compliance & Regulatory", roles: ["SUPER_ADMIN", "COMPLIANCE", "QA", "MANAGEMENT"], section: "Governance & External" },
  { to: "/pv", label: "Pharmacovigilance (PV)", roles: ["SUPER_ADMIN", "QA", "COMPLIANCE", "PHARMACIST", "MANAGEMENT"], section: "Governance & External" },
  { to: "/audit", label: "Auditor & Traceability", roles: ["SUPER_ADMIN", "AUDITOR", "COMPLIANCE", "QA", "MANAGEMENT"], section: "Governance & External" },
  { to: "/ai-governance", label: "AI Governance & Models", roles: ["SUPER_ADMIN", "MANAGEMENT"], section: "Governance & External" },
  { to: "/masters", label: "Master Data Steward", roles: ["SUPER_ADMIN", "MANAGEMENT", "PLANNING", "QA"], section: "Governance & External" },
  { to: "/portals", label: "External Portals View", roles: ["SUPER_ADMIN", "MANAGEMENT", "SUPPLIER", "CUSTOMER"], section: "Governance & External" },
  { to: "/admin", label: "System Administration", roles: ["SUPER_ADMIN"], section: "Governance & External" },
];

function visibleNav(me) {
  const roles = me?.roles || [];
  if (!roles.length || roles.includes("SUPER_ADMIN") || roles.includes("MANAGEMENT")) {
    return NAV_ITEMS;
  }
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
    if (n.section !== last) {
      out.push({ isDivider: true, section: n.section });
      last = n.section;
    }
    out.push(n);
  }
  return out;
}

// ── Role Access Gatekeeper Component ──
function AccessDenied({ requiredRoles, userRoles }) {
  const navigate = useNavigate();
  const homePath = getRoleHome(userRoles);

  return (
    <div style={{ padding: 40, maxWidth: 640, margin: "60px auto", textAlign: "center" }}>
      <div style={{ fontSize: 56, marginBottom: 16 }}>⛔</div>
      <h2 style={{ marginBottom: 8 }}>Access Restricted (403 Forbidden)</h2>
      <p style={{ color: "var(--muted)", lineHeight: 1.6, marginBottom: 24 }}>
        Your user identity has role(s):{" "}
        <b>{userRoles?.length ? userRoles.join(", ") : "GUEST"}</b>.
        <br />
        This workspace requires:{" "}
        <span className="mono" style={{ color: "var(--purple)", fontWeight: 600 }}>
          {Array.isArray(requiredRoles) ? requiredRoles.join(" · ") : requiredRoles}
        </span>
      </p>
      <div className="row" style={{ justifyContent: "center", gap: 12 }}>
        <button className="btn primary" onClick={() => navigate(homePath)}>
          Return to My Workspace
        </button>
        <button className="btn ghost" onClick={() => navigate("/profile")}>
          View Profile & Permissions
        </button>
      </div>
    </div>
  );
}

// ── Protected Route Guard ──
function ProtectedRoute({ element, allowedRoles }) {
  const me = getMe();
  const roles = me?.roles || [];

  // Super Admin and Management can access all internal workspaces
  if (roles.includes("SUPER_ADMIN") || roles.includes("MANAGEMENT")) {
    return element;
  }

  // Common internal suite
  if (allowedRoles === "any") {
    const isExternal = roles.includes("SUPPLIER") || roles.includes("CUSTOMER");
    if (!isExternal) return element;
  }

  const isAllowed = Array.isArray(allowedRoles) && allowedRoles.some((r) => roles.includes(r));
  if (!isAllowed) {
    return <AccessDenied requiredRoles={allowedRoles} userRoles={roles} />;
  }

  return element;
}

// ── Home Route Router ──
function HomeRedirect() {
  const me = getMe();
  const roles = me?.roles || [];
  if (roles.includes("SUPER_ADMIN") || roles.includes("MANAGEMENT")) {
    return <CommandCenter />;
  }
  const home = getRoleHome(roles);
  return <Navigate to={home} replace />;
}

function Shell({ children, onOpenSearch }) {
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
          <div className="logo-badge">Pharma AI OS</div>
          <small>Autonomous Enterprise Core</small>
        </div>
        <div className="sidebar-nav">
          {navWithSections(me).map((n, i) =>
            n.isDivider ? (
              <div className="nav-section" key={`s${i}`}>{n.section.toUpperCase()}</div>
            ) : (
              <NavLink key={n.to} to={n.to} end={n.end}
                       className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}>
                <span className="nav-label">{n.label}</span>
                {n.pill === "human" && pending > 0 && <span className="pill">{pending}</span>}
              </NavLink>
            )
          )}
        </div>
        <div className="sidebar-foot">
          <button className="btn ghost sm" style={{ width: "100%", marginBottom: 8 }} onClick={logout}>
            Sign out
          </button>
        </div>
      </aside>
      <main className="main">{children}</main>
    </div>
  );
}

export function Topbar({ title, sub }) {
  const me = getMe();
  const navigate = useNavigate();
  const displayName = me?.name || me?.email?.split("@")[0] || "User";
  const rolesList = (me?.roles || []).join(" · ") || "Operator";
  const initials = (me?.name ? me.name.split(" ").map(w => w[0]).join("") : me?.email || "U").slice(0, 2).toUpperCase();

  const isElevated = (me?.roles || []).includes("SUPER_ADMIN") || (me?.roles || []).includes("MANAGEMENT");

  return (
    <div className="topbar">
      <div className="topbar-header">
        <h1>{title}</h1>
        {sub && <div className="sub">{sub}</div>}
      </div>
      <div className="spacer" />

      {/* Instant Global Search Trigger */}
      <button
        className="topbar-search-btn"
        onClick={() => window.dispatchEvent(new CustomEvent("open-global-search"))}
        title="Search records across all modules (Cmd+K)"
      >
        <span>Search...</span>
        <kbd>⌘K</kbd>
      </button>

      {/* Role Perspective Switcher for Admins */}
      {isElevated && (
        <select
          className="role-switcher-select"
          onChange={(e) => { if (e.target.value) navigate(e.target.value); }}
          defaultValue=""
          title="Jump to Role Workspace"
        >
          <option value="" disabled>Switch Workspace...</option>
          <option value="/">AI Command Center</option>
          <option value="/executive">Executive Control Tower</option>
          <option value="/supply">Supply Chain Planning</option>
          <option value="/sales">Sales & CRM</option>
          <option value="/vendors">Procurement & Sourcing</option>
          <option value="/warehouse">Warehouse & WMS</option>
          <option value="/quality">QC / LIMS · QA / QMS</option>
          <option value="/plant">Plant & Production</option>
          <option value="/engineering">Engineering & Maintenance</option>
          <option value="/pharmacy">Pharmacy & POS Counter</option>
          <option value="/logistics">Logistics & Fleet</option>
          <option value="/finance">Finance & Accounts</option>
          <option value="/compliance">Compliance & Regulatory</option>
          <option value="/pv">Pharmacovigilance (PV)</option>
          <option value="/audit">Auditor & Traceability</option>
          <option value="/ai-governance">AI Governance & Models</option>
          <option value="/masters">Master Data Steward</option>
          <option value="/portals">External Portals View</option>
          <option value="/admin">System Administration</option>
        </select>
      )}

      <span className="live-chip"><span className="dot green pulse" /> Live — agents orchestrating</span>

      <div className="user-chip clickable" onClick={() => navigate("/profile")} title="View Profile">
        <div className="avatar">{initials}</div>
        <div className="user-chip-info">
          <b>{displayName}</b>
          <small>{rolesList}</small>
        </div>
      </div>
    </div>
  );
}

export default function App() {
  const [authed, setAuthed] = useState(!!getToken());
  const [searchOpen, setSearchOpen] = useState(false);

  useEffect(() => onAuthChange(() => setAuthed(!!getToken())), []);

  useEffect(() => {
    if (getToken()) {
      api("/api/auth/me")
        .then((user) => {
          if (user) {
            setAuth(getToken(), user, getRefresh());
          }
        })
        .catch(() => { /* silent */ });
    }
  }, [authed]);

  // Global Cmd+K / Ctrl+K keyboard shortcut listener
  useEffect(() => {
    const handleKeyDown = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setSearchOpen((prev) => !prev);
      }
    };
    const handleCustom = () => setSearchOpen(true);
    window.addEventListener("keydown", handleKeyDown);
    window.addEventListener("open-global-search", handleCustom);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
      window.removeEventListener("open-global-search", handleCustom);
    };
  }, []);

  return (
    <ToastHost>
      <BrowserRouter>
        <GlobalSearchModal open={searchOpen} onClose={() => setSearchOpen(false)} />
        {!authed ? (
          <Routes>
            <Route path="*" element={<Login />} />
          </Routes>
        ) : (
          <Shell onOpenSearch={() => setSearchOpen(true)}>
            <Routes>
              {/* Home & Overview */}
              <Route path="/" element={<HomeRedirect />} />
              <Route path="/executive" element={<ProtectedRoute allowedRoles={["SUPER_ADMIN", "MANAGEMENT"]} element={<ExecutiveDashboard />} />} />

              {/* Core Workspaces */}
              <Route path="/supply" element={<ProtectedRoute allowedRoles={["SUPER_ADMIN", "PLANNING", "MANAGEMENT", "PROCUREMENT", "BUYER", "SALES", "WAREHOUSE"]} element={<SupplyWorkspace />} />} />
              <Route path="/sales" element={<ProtectedRoute allowedRoles={["SUPER_ADMIN", "SALES", "MANAGEMENT", "LOGISTICS", "FINANCE"]} element={<SalesWorkspace />} />} />
              <Route path="/vendors" element={<ProtectedRoute allowedRoles={["SUPER_ADMIN", "PROCUREMENT", "BUYER", "VENDOR_MANAGER", "FINANCE", "QA", "MANAGEMENT"]} element={<VendorsWorkspace />} />} />
              <Route path="/procurement" element={<ProtectedRoute allowedRoles={["SUPER_ADMIN", "PROCUREMENT", "BUYER", "VENDOR_MANAGER", "FINANCE", "QA", "MANAGEMENT"]} element={<VendorsWorkspace />} />} />
              <Route path="/warehouse" element={<ProtectedRoute allowedRoles={["SUPER_ADMIN", "WAREHOUSE", "LOGISTICS", "MANAGEMENT", "QC", "PLANNING"]} element={<WarehouseWorkspace />} />} />
              <Route path="/quality" element={<ProtectedRoute allowedRoles={["SUPER_ADMIN", "QA", "QC", "MANAGEMENT", "COMPLIANCE", "PLANT"]} element={<QualityWorkspace />} />} />
              <Route path="/plant" element={<ProtectedRoute allowedRoles={["SUPER_ADMIN", "PLANT", "QA", "MANAGEMENT", "PLANNING"]} element={<PlantWorkspace />} />} />
              <Route path="/engineering" element={<ProtectedRoute allowedRoles={["SUPER_ADMIN", "PLANT", "QC", "MANAGEMENT"]} element={<EngineeringWorkspace />} />} />
              <Route path="/pharmacy" element={<ProtectedRoute allowedRoles={["SUPER_ADMIN", "PHARMACIST", "SALES", "MANAGEMENT"]} element={<PharmacyWorkspace />} />} />
              <Route path="/pos" element={<ProtectedRoute allowedRoles={["SUPER_ADMIN", "PHARMACIST", "SALES", "MANAGEMENT"]} element={<PharmacyWorkspace />} />} />
              <Route path="/logistics" element={<ProtectedRoute allowedRoles={["SUPER_ADMIN", "LOGISTICS", "WAREHOUSE", "SALES", "MANAGEMENT"]} element={<LogisticsWorkspace />} />} />
              <Route path="/finance" element={<ProtectedRoute allowedRoles={["SUPER_ADMIN", "FINANCE", "MANAGEMENT", "AUDITOR", "COMPLIANCE"]} element={<FinanceWorkspace />} />} />

              {/* Common Suite */}
              <Route path="/queue" element={<ProtectedRoute allowedRoles="any" element={<DecisionQueue />} />} />
              <Route path="/exceptions" element={<ProtectedRoute allowedRoles="any" element={<ExceptionCenter />} />} />
              <Route path="/documents" element={<ProtectedRoute allowedRoles="any" element={<DocumentCenter />} />} />
              <Route path="/workflows" element={<ProtectedRoute allowedRoles="any" element={<WorkflowViewer />} />} />
              <Route path="/agents" element={<ProtectedRoute allowedRoles="any" element={<AgentActivity />} />} />
              <Route path="/sop-training" element={<ProtectedRoute allowedRoles="any" element={<SopTraining />} />} />
              <Route path="/profile" element={<ProtectedRoute allowedRoles="any" element={<UserProfile />} />} />

              {/* Regulatory & Governance */}
              <Route path="/compliance" element={<ProtectedRoute allowedRoles={["SUPER_ADMIN", "COMPLIANCE", "QA", "MANAGEMENT"]} element={<ComplianceWorkspace />} />} />
              <Route path="/pv" element={<ProtectedRoute allowedRoles={["SUPER_ADMIN", "QA", "COMPLIANCE", "PHARMACIST", "MANAGEMENT"]} element={<SafetyWorkspace />} />} />
              <Route path="/audit" element={<ProtectedRoute allowedRoles={["SUPER_ADMIN", "AUDITOR", "COMPLIANCE", "QA", "MANAGEMENT"]} element={<AuditWorkspace />} />} />
              <Route path="/ai-governance" element={<ProtectedRoute allowedRoles={["SUPER_ADMIN", "MANAGEMENT"]} element={<AiGovernanceWorkspace />} />} />
              <Route path="/masters" element={<ProtectedRoute allowedRoles={["SUPER_ADMIN", "MANAGEMENT", "PLANNING", "QA"]} element={<MasterDataWorkspace />} />} />
              <Route path="/portals" element={<ProtectedRoute allowedRoles={["SUPER_ADMIN", "MANAGEMENT", "SUPPLIER", "CUSTOMER"]} element={<PortalsWorkspace />} />} />
              <Route path="/portal/supplier" element={<ProtectedRoute allowedRoles={["SUPER_ADMIN", "MANAGEMENT", "SUPPLIER"]} element={<PortalsWorkspace />} />} />
              <Route path="/portal/customer" element={<ProtectedRoute allowedRoles={["SUPER_ADMIN", "MANAGEMENT", "CUSTOMER"]} element={<PortalsWorkspace />} />} />
              <Route path="/admin" element={<ProtectedRoute allowedRoles={["SUPER_ADMIN"]} element={<AdminPage />} />} />

              {/* Fallback */}
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </Shell>
        )}
      </BrowserRouter>
    </ToastHost>
  );
}
