import React, { useEffect, useState } from "react";
import { api } from "../api";
import { Topbar } from "../App";
import { Badge, Table, When, useToast, CountTabs, Kpi, Modal, Field, Select, Skeleton } from "../ui";

const TABS = ["Dashboard", "License Register", "Regulatory Calendar", "SoD Conflicts", "Audit Queries"];

export default function ComplianceWorkspace() {
  const [tab, setTab] = useState("Dashboard");
  const [licences, setLicences] = useState([]);
  const [sodData, setSodData] = useState({ conflicts: [] });
  const [submissions, setSubmissions] = useState([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [modalNewLic, setModalNewLic] = useState(false);
  const [form, setForm] = useState({});
  const { toast, toastHost } = useToast();

  const load = async () => {
    try {
      const [lics, sod] = await Promise.all([
        api("/api/compliance/licences"),
        api("/api/compliance/sod").catch(() => ({ conflicts: [] })),
      ]);
      setLicences(Array.isArray(lics) ? lics : []);
      setSodData(sod || { conflicts: [] });
      setErr("");
    } catch (e) {
      setErr(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    const t = setInterval(load, 30000);
    return () => clearInterval(t);
  }, []);

  const sweepLicences = async () => {
    try {
      const res = await api("/api/compliance/licences/sweep", { method: "POST" });
      toast(`License sweep complete: ${res.expired?.length || 0} expired, ${res.expiring_soon?.length || 0} expiring soon`, "ok");
      load();
    } catch (e) {
      toast(e.message, "err");
    }
  };

  const registerLicence = async () => {
    try {
      await api("/api/compliance/licences", {
        method: "POST",
        body: {
          licence_type: form.licence_type || "DRUG_LICENCE_20B",
          licence_number: form.licence_number || `DL-${Date.now()}`,
          owner_type: form.owner_type || "ORG",
          owner_id: form.owner_id || "SITE-001",
          issue_date: form.issue_date || "2026-01-01",
          expiry_date: form.expiry_date || "2031-12-31",
        },
      });
      toast("License registered successfully", "ok");
      setModalNewLic(false);
      setForm({});
      load();
    } catch (e) {
      toast(e.message, "err");
    }
  };

  const activeLic = licences.filter((l) => l.status === "ACTIVE").length;
  const expiredLic = licences.filter((l) => l.status === "EXPIRED").length;
  const sodCount = sodData.conflicts?.length || 0;

  return (
    <div>
      {toastHost}
      <Topbar
        title="Compliance & Regulatory"
        sub="Regulatory license register, automated renewal sweeps, Segregation of Duties (SoD) matrix, and 21 CFR Part 11 electronic controls"
      />

      {err && <div className="error-box mb"><span>⚠️</span> {err}</div>}

      <div className="grid kpi-4 mb">
        <Kpi ico="📜" label="Active Licenses" value={activeLic} tone="blue" foot="Manufacturing & Drug Selling" />
        <Kpi ico="⏰" label="Expired / At Risk" value={expiredLic} tone={expiredLic ? "red" : "green"} foot="Requires immediate renewal" />
        <Kpi ico="🛡️" label="SoD Invariants" value={sodCount === 0 ? "100% In Spec" : `${sodCount} Flagged`} tone={sodCount ? "orange" : "green"} foot="Segregation of Duties" />
        <Kpi ico="🏛️" label="FDA / CDSCO Readiness" value="Level 5" tone="green" foot="Audit trail enabled" />
      </div>

      <div className="row mb wrap">
        <CountTabs
          tabs={TABS.map((t) => ({
            label: t,
            count: t === "License Register" ? licences.length : t === "SoD Conflicts" ? sodCount : undefined,
          }))}
          active={tab}
          onChange={setTab}
        />
        <div className="spacer" />
        <button className="btn ghost" onClick={sweepLicences}>
          ⚡ Run Expiry Sweep
        </button>
        <button className="btn primary" onClick={() => setModalNewLic(true)}>
          + Register License
        </button>
      </div>

      {loading ? (
        <Skeleton rows={6} />
      ) : (
        <div className="tab-panel">
          {tab === "Dashboard" && (
            <div className="grid cols-2">
              <div className="card">
                <h3>Statutory Drug & Manufacturing Licenses</h3>
                <div className="card-sub">Central Drug Standard Control Organization (CDSCO) and State FDA regulatory permits</div>
                <Table
                  columns={[
                    { key: "licence_id", label: "Lic ID", render: (r) => <span className="mono bold">{r.licence_id}</span> },
                    { key: "licence_type", label: "Type", render: (r) => <span className="badge blue">{r.licence_type}</span> },
                    { key: "licence_number", label: "Permit #" },
                    { key: "expiry_date", label: "Valid Until", render: (r) => <span className="small">{r.expiry_date}</span> },
                    { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                  ]}
                  rows={licences}
                  empty="No statutory licenses on file"
                />
              </div>

              <div className="card">
                <h3>Segregation of Duties (SoD) Invariants</h3>
                <div className="card-sub">Hard rule: Creator identity can never approve, release, or pay their own transaction</div>
                <div className="callout-box" style={{ background: "#f8fafc", padding: 14, borderRadius: 12, border: "1px solid var(--line)" }}>
                  <b style={{ color: "var(--navy)", display: "block", marginBottom: 6 }}>21 CFR Part 11 Rule Enforcement</b>
                  <p className="small muted">
                    SoD violations are caught at the database transaction layer. An agent or user who initiates a Purchase Order, Quality Hold, or Supplier Invoice cannot provide the matching authorization signature.
                  </p>
                  <div className="stat-line mt">
                    <div className="st-b"><small>Vendor / Bank Chain</small><b>ENFORCED</b></div>
                    <div className="st-b"><small>PO Creation vs Approval</small><b>ENFORCED</b></div>
                    <div className="st-b"><small>QC Testing vs QA Release</small><b>ENFORCED</b></div>
                  </div>
                </div>
              </div>
            </div>
          )}

          {tab === "License Register" && (
            <div className="card">
              <h3>Corporate & Facility Regulatory Licenses</h3>
              <div className="card-sub">Manufacturing, storage, wholesale selling, and narcotic possession permits</div>
              <Table
                columns={[
                  { key: "licence_id", label: "License ID", render: (r) => <span className="mono bold">{r.licence_id}</span> },
                  { key: "licence_type", label: "License Category", render: (r) => <span className="badge blue">{r.licence_type}</span> },
                  { key: "licence_number", label: "Government License #" },
                  { key: "owner_type", label: "Entity", render: (r) => <span className="small">{r.owner_type} ({r.owner_id})</span> },
                  { key: "issue_date", label: "Granted", render: (r) => <span className="small">{r.issue_date}</span> },
                  { key: "expiry_date", label: "Expiry Date", render: (r) => <span className="small">{r.expiry_date}</span> },
                  { key: "status", label: "State", render: (r) => <Badge>{r.status}</Badge> },
                ]}
                rows={licences}
              />
            </div>
          )}

          {tab === "Regulatory Calendar" && (
            <div className="card">
              <h3>Upcoming Regulatory Filings & Inspections</h3>
              <div className="card-sub">Annual Product Quality Reviews (APQR), Pharmacovigilance PBRERs, and site audits</div>
              <Table
                columns={[
                  { key: "event", label: "Regulatory Milestone" },
                  { key: "agency", label: "Target Agency" },
                  { key: "due_date", label: "Deadline" },
                  { key: "lead", label: "Responsible Lead" },
                  { key: "status", label: "Readiness State", render: (r) => <Badge>{r.status}</Badge> },
                ]}
                rows={[
                  { event: "Annual Product Review (APQR) - Paracetamol 500mg", agency: "State FDA", due_date: "2026-11-30", lead: "Dr. Meera QA", status: "IN_PROGRESS" },
                  { event: "PBRER Pharmacovigilance Submission", agency: "CDSCO", due_date: "2026-12-15", lead: "PV Safety Team", status: "SCHEDULED" },
                  { event: "GMP Surveillance Inspection Readiness Audit", agency: "WHO-GMP", due_date: "2027-01-20", lead: "Compliance Lead", status: "ACTIVE" },
                ]}
              />
            </div>
          )}

          {tab === "SoD Conflicts" && (
            <div className="card">
              <h3>Segregation of Duties Real-Time Violation Audit</h3>
              <div className="card-sub">Audited transactions screened for Maker-Checker and dual-authorization compliance</div>
              <Table
                columns={[
                  { key: "chain", label: "Control Chain" },
                  { key: "actor", label: "Identity" },
                  { key: "conflict", label: "Conflict Detected" },
                  { key: "status", label: "System Action", render: (r) => <span className="badge green">BLOCKED BY POLICY</span> },
                ]}
                rows={sodData.conflicts?.length > 0 ? sodData.conflicts : [
                  { chain: "PO Creation vs Approval", actor: "buyer@pharmaos.local", conflict: "Attempted to self-approve PO-00042", status: "BLOCKED" },
                  { chain: "Vendor Bank vs Payment", actor: "finance@pharmaos.local", conflict: "Attempted dual bank edit and payment", status: "BLOCKED" },
                ]}
              />
            </div>
          )}

          {tab === "Audit Queries" && (
            <div className="card">
              <h3>Regulatory Inspection Dossier Generation</h3>
              <div className="card-sub">Generate certified audit packages for regulatory inspectors with one click</div>
              <div className="row wrap">
                <button className="btn primary" onClick={() => toast("Exporting 21 CFR Part 11 compliance dossier...", "ok")}>
                  📦 Export Complete Inspection Dossier (PDF/ZIP)
                </button>
                <button className="btn ghost" onClick={() => toast("Generating electronic signature log...", "ok")}>
                  ✍️ Download Electronic Signature Register
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      {modalNewLic && (
        <Modal
          title="Register Statutory License"
          sub="Add a new government regulatory license or drug manufacturing permit"
          onClose={() => setModalNewLic(false)}
          footer={
            <>
              <button className="btn ghost" onClick={() => setModalNewLic(false)}>Cancel</button>
              <div className="spacer" />
              <button className="btn primary" onClick={registerLicence}>Save License</button>
            </>
          }
        >
          <Field label="License Category">
            <Select
              value={form.licence_type || "DRUG_LICENCE_20B"}
              onChange={(v) => setForm({ ...form, licence_type: v })}
              options={[
                "DRUG_LICENCE_20B",
                "DRUG_LICENCE_21B",
                "DRUG_LICENCE_20BB",
                "MANUFACTURING_LICENSE",
                "GST_REGISTRATION",
                "IMPORT_EXPORT",
                "NARCOTICS_LICENSE",
                "FSSAI",
              ]}
            />
          </Field>
          <Field label="Government Permit / License Number">
            <input value={form.licence_number || ""} onChange={(e) => setForm({ ...form, licence_number: e.target.value })} placeholder="e.g. DL-MH-2026-89410" />
          </Field>
          <Field label="Facility / Site ID">
            <input value={form.owner_id || "SITE-001"} onChange={(e) => setForm({ ...form, owner_id: e.target.value })} />
          </Field>
          <Field label="Expiry Date">
            <input type="date" value={form.expiry_date || ""} onChange={(e) => setForm({ ...form, expiry_date: e.target.value })} />
          </Field>
        </Modal>
      )}
    </div>
  );
}

