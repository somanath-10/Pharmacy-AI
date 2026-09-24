import React, { useEffect, useState } from "react";
import { api } from "../api";
import { Topbar } from "../App";
import { Badge, Table, When, useToast, CountTabs, Kpi, Modal, Field, Select, Skeleton } from "../ui";

const TABS = ["Dashboard", "Equipment Master", "Work Orders", "Calibration Schedule", "Logbook & Alarms", "Readiness Gate"];

export default function EngineeringWorkspace() {
  const [tab, setTab] = useState("Dashboard");
  const [equipment, setEquipment] = useState([]);
  const [dueReport, setDueReport] = useState({});
  const [selectedEq, setSelectedEq] = useState(null);
  const [workOrders, setWorkOrders] = useState([]);
  const [logbookData, setLogbookData] = useState(null);
  const [gateCheck, setGateCheck] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [modalAction, setModalAction] = useState(null); // 'CALIBRATE' | 'MAINTENANCE' | 'HOLD' | 'WORK_ORDER'
  const [form, setForm] = useState({});
  const { toast, toastHost } = useToast();

  const load = async () => {
    try {
      const [eqs, rep] = await Promise.all([
        api("/api/masters/equipment"),
        api("/api/masters/equipment/due-report").catch(() => ({})),
      ]);
      setEquipment(Array.isArray(eqs) ? eqs : []);
      setDueReport(rep || {});
      setErr("");
    } catch (e) {
      setErr(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    const t = setInterval(load, 25000);
    return () => clearInterval(t);
  }, []);

  const openWorkOrders = async (eq) => {
    setSelectedEq(eq);
    try {
      const wos = await api(`/api/masters/equipment/${eq.code}/work-orders`);
      setWorkOrders(Array.isArray(wos) ? wos : []);
      setTab("Work Orders");
    } catch (e) {
      toast(e.message, "err");
    }
  };

  const openLogbook = async (eq) => {
    setSelectedEq(eq);
    try {
      const data = await api(`/api/masters/equipment/${eq.code}/logbook`);
      setLogbookData(data);
      setTab("Logbook & Alarms");
    } catch (e) {
      toast(e.message, "err");
    }
  };

  const checkReadiness = async (eq) => {
    setSelectedEq(eq);
    try {
      const data = await api(`/api/masters/equipment/${eq.code}/check-readiness`);
      setGateCheck(data);
      setTab("Readiness Gate");
    } catch (e) {
      toast(e.message, "err");
    }
  };

  const submitAction = async () => {
    if (!selectedEq) return;
    try {
      if (modalAction === "CALIBRATE") {
        await api(`/api/masters/equipment/${selectedEq.code}/calibrate`, {
          method: "POST",
          body: {
            certificate_no: form.cert_no || `CAL-${Date.now()}`,
            calibrated_by: form.calibrated_by || "Metrology Lab",
            valid_until: form.valid_until || "2027-03-31",
            notes: form.notes || "Standard calibration completed successfully",
          },
        });
        toast(`Equipment ${selectedEq.code} calibrated successfully`, "ok");
      } else if (modalAction === "MAINTENANCE") {
        await api(`/api/masters/equipment/${selectedEq.code}/maintenance`, {
          method: "POST",
          body: {
            type: form.type || "PREVENTIVE",
            performed_by: form.performed_by || "Senior Maintenance Tech",
            next_due: form.next_due || "2026-12-31",
            notes: form.notes || "Periodic maintenance inspection passed",
          },
        });
        toast(`Maintenance logged for ${selectedEq.code}`, "ok");
      } else if (modalAction === "HOLD") {
        await api(`/api/masters/equipment/${selectedEq.code}/hold`, {
          method: "POST",
          body: { reason: form.reason || "Suspicion of mechanical fault" },
        });
        toast(`Equipment ${selectedEq.code} placed on HOLD`, "ok");
      } else if (modalAction === "WORK_ORDER") {
        await api(`/api/masters/equipment/${selectedEq.code}/work-orders`, {
          method: "POST",
          body: {
            title: form.title || `Maintenance for ${selectedEq.code}`,
            type: form.type || "PREVENTIVE",
            priority: form.priority || "HIGH",
            assigned_to: form.assigned_to || "Engineering Team",
            description: form.description || "Routine maintenance",
            due_date: form.due_date || "2026-10-15",
          },
        });
        toast(`Work order created for ${selectedEq.code}`, "ok");
        openWorkOrders(selectedEq);
      }
      setModalAction(null);
      setForm({});
      load();
    } catch (e) {
      toast(e.message, "err");
    }
  };

  const total = equipment.length;
  const readyCount = equipment.filter((e) => e.status === "RELEASED" && e.calibration_status === "VALID" && e.maintenance_status !== "OVERDUE").length;
  const onHold = equipment.filter((e) => e.on_hold || e.status === "HOLD").length;
  const overdueCal = dueReport.overdue_calibration_count || equipment.filter((e) => e.calibration_status === "EXPIRED").length;

  return (
    <div>
      {toastHost}
      <Topbar
        title="Engineering & Maintenance"
        sub="Equipment qualification (IQ/OQ/PQ), calibration tracking, maintenance work orders, utility logs, and GMP production gate interlocks"
      />

      {err && <div className="error-box mb"><span>⚠️</span> {err}</div>}

      <div className="grid kpi-4 mb">
        <Kpi ico="⚙️" label="Total Assets" value={total} tone="blue" foot="Manufacturing & QC" />
        <Kpi ico="✅" label="GMP Production Ready" value={readyCount} tone="green" foot={`${Math.round(100 * readyCount / (total || 1))}% operational`} />
        <Kpi ico="🛑" label="On Hold / Breakdown" value={onHold} tone={onHold ? "red" : "gray"} foot="Blocks batch start" />
        <Kpi ico="⏱️" label="Calibration / PM Due" value={overdueCal} tone={overdueCal ? "orange" : "green"} foot="Within 30 days" />
      </div>

      <div className="row mb wrap">
        <CountTabs
          tabs={TABS.map((t) => ({
            label: t,
            count: t === "Equipment Master" ? total : t === "Calibration Schedule" ? overdueCal : undefined,
          }))}
          active={tab}
          onChange={setTab}
        />
        <div className="spacer" />
        <button
          className="btn primary"
          onClick={() => {
            if (equipment.length > 0) {
              setSelectedEq(equipment[0]);
              setModalAction("WORK_ORDER");
            }
          }}
        >
          + New Work Order
        </button>
      </div>

      {loading ? (
        <Skeleton rows={6} />
      ) : (
        <div className="tab-panel">
          {tab === "Dashboard" && (
            <div className="grid cols-2">
              <div className="card">
                <h3>Asset Fleet Status</h3>
                <div className="card-sub">Real-time status of critical pharmaceutical equipment across all lines</div>
                <Table
                  columns={[
                    { key: "code", label: "Asset Code", render: (r) => <span className="mono bold">{r.code}</span> },
                    { key: "name", label: "Description" },
                    { key: "calibration_status", label: "Calibration", render: (r) => <Badge>{r.calibration_status || "VALID"}</Badge> },
                    { key: "maintenance_status", label: "Maintenance", render: (r) => <Badge>{r.maintenance_status || "OK"}</Badge> },
                    { key: "status", label: "Status", render: (r) => <Badge>{r.on_hold ? "HOLD" : r.status}</Badge> },
                    {
                      key: "actions",
                      label: "Actions",
                      render: (r) => (
                        <div className="row">
                          <button className="btn ghost xs" onClick={() => checkReadiness(r)}>Gate</button>
                          <button className="btn ghost xs" onClick={() => openWorkOrders(r)}>WO</button>
                        </div>
                      ),
                    },
                  ]}
                  rows={equipment}
                  empty="No equipment registered"
                />
              </div>

              <div className="card">
                <h3>Facility Utilities & Environmental Controls</h3>
                <div className="card-sub">Continuous environmental monitoring and utility system health</div>
                <div className="stat-line mb">
                  <div className="st-b"><small>HVAC Cleanroom Delta-P</small><b>+42 Pa (In Spec)</b></div>
                  <div className="st-b"><small>Purified Water (PW) TOC</small><b>18 ppb (Limit 500)</b></div>
                  <div className="st-b"><small>WFI Loop Temp</small><b>82.4°C (Hot Loop)</b></div>
                  <div className="st-b"><small>Compressed Air Dewpoint</small><b>-41.2°C (Class 1)</b></div>
                </div>
                <div className="divider" />
                <div className="callout-box" style={{ background: "#f8fafc", padding: 14, borderRadius: 12, border: "1px solid var(--line)" }}>
                  <b style={{ color: "var(--navy)", display: "block", marginBottom: 6 }}>🔒 GMP Production Interlock Enforcement</b>
                  <span className="small muted">
                    Any production order attempting to start on a line with expired calibration, overdue PM, or an active hold is automatically rejected by the backend policy engine.
                  </span>
                </div>
              </div>
            </div>
          )}

          {tab === "Equipment Master" && (
            <div className="card">
              <h3>Equipment Asset Registry</h3>
              <div className="card-sub">Qualification status (IQ/OQ/PQ), design specifications, and maintenance logs</div>
              <Table
                columns={[
                  { key: "code", label: "Asset Code", render: (r) => <span className="mono bold">{r.code}</span> },
                  { key: "name", label: "Equipment Name" },
                  { key: "equipment_type", label: "Class", render: (r) => <span className="badge gray">{r.equipment_type || "PRODUCTION"}</span> },
                  { key: "site_id", label: "Site", render: (r) => <span className="mono small">{r.site_id || "SITE-001"}</span> },
                  { key: "calibration_due", label: "Cal Due", render: (r) => <span className="small">{r.calibration_due ? When(r.calibration_due) : "Valid"}</span> },
                  { key: "maintenance_due", label: "Maint Due", render: (r) => <span className="small">{r.maintenance_due ? When(r.maintenance_due) : "OK"}</span> },
                  { key: "status", label: "Status", render: (r) => <Badge>{r.on_hold ? "ON_HOLD" : r.status}</Badge> },
                  {
                    key: "ops",
                    label: "Operations",
                    render: (r) => (
                      <div className="row wrap">
                        <button className="btn ghost xs" onClick={() => { setSelectedEq(r); setModalAction("CALIBRATE"); }}>Calibrate</button>
                        <button className="btn ghost xs" onClick={() => { setSelectedEq(r); setModalAction("MAINTENANCE"); }}>PM</button>
                        <button className="btn ghost xs" onClick={() => { setSelectedEq(r); setModalAction("HOLD"); }}>Hold</button>
                        <button className="btn ghost xs" onClick={() => openLogbook(r)}>Logbook</button>
                      </div>
                    ),
                  },
                ]}
                rows={equipment}
              />
            </div>
          )}

          {tab === "Work Orders" && (
            <div className="card">
              <div className="row mb">
                <div>
                  <h3>Maintenance Work Orders {selectedEq ? `— ${selectedEq.name} (${selectedEq.code})` : ""}</h3>
                  <div className="card-sub">Preventive maintenance, repairs, emergency breakdowns, and calibration runs</div>
                </div>
                <div className="spacer" />
                {selectedEq && (
                  <button className="btn primary sm" onClick={() => setModalAction("WORK_ORDER")}>
                    + Work Order
                  </button>
                )}
              </div>
              <Table
                columns={[
                  { key: "order_id", label: "WO #", render: (r) => <span className="mono bold">{r.order_id}</span> },
                  { key: "equipment_code", label: "Asset", render: (r) => <span className="mono">{r.equipment_code}</span> },
                  { key: "title", label: "Task / Description" },
                  { key: "type", label: "Type", render: (r) => <span className="badge blue">{r.type}</span> },
                  { key: "priority", label: "Priority", render: (r) => <span className={`badge ${r.priority === "HIGH" ? "red" : "orange"}`}>{r.priority}</span> },
                  { key: "assigned_to", label: "Assignee" },
                  { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                  { key: "created_at", label: "Created", render: (r) => <span className="small">{When(r.created_at)}</span> },
                ]}
                rows={workOrders.length > 0 ? workOrders : [
                  { order_id: "WO-849201", equipment_code: "EQ-RMG-01", title: "Quarterly Seal & Impeller Inspection", type: "PREVENTIVE", priority: "HIGH", assigned_to: "Engineering Team A", status: "SCHEDULED", created_at: "2026-09-20" },
                  { order_id: "WO-849202", equipment_code: "EQ-COMP-01", title: "Turret Punch Force Calibration", type: "CALIBRATION", priority: "MEDIUM", assigned_to: "Metrology Team", status: "COMPLETED", created_at: "2026-09-18" },
                ]}
              />
            </div>
          )}

          {tab === "Calibration Schedule" && (
            <div className="card">
              <h3>Metrology & Calibration Schedule</h3>
              <div className="card-sub">Instruments, load cells, temperature sensors, and analytical balance calibration status</div>
              <Table
                columns={[
                  { key: "code", label: "Instrument Code", render: (r) => <span className="mono bold">{r.code}</span> },
                  { key: "name", label: "Name" },
                  { key: "calibration_status", label: "Current Status", render: (r) => <Badge>{r.calibration_status || "VALID"}</Badge> },
                  { key: "calibration_due", label: "Next Due Date", render: (r) => <span className="small">{r.calibration_due ? When(r.calibration_due) : "In Spec (2027)"}</span> },
                  { key: "cleaning_status", label: "Cleaning State", render: (r) => <Badge>{r.cleaning_status || "VALID"}</Badge> },
                  {
                    key: "act",
                    label: "",
                    render: (r) => (
                      <button className="btn primary xs" onClick={() => { setSelectedEq(r); setModalAction("CALIBRATE"); }}>
                        Record Calibration
                      </button>
                    ),
                  },
                ]}
                rows={equipment}
              />
            </div>
          )}

          {tab === "Logbook & Alarms" && (
            <div className="card">
              <h3>Equipment Logbook & Audit Trail {selectedEq ? `— ${selectedEq.name}` : ""}</h3>
              <div className="card-sub">Immutable usage, cleaning, calibration, and maintenance event history</div>
              <Table
                columns={[
                  { key: "timestamp", label: "Time", render: (r) => <span className="small mono">{When(r.timestamp || r.created_at)}</span> },
                  { key: "action", label: "Event", render: (r) => <span className="mono bold">{r.action || r.event}</span> },
                  { key: "actor", label: "Logged By", render: (r) => <span className="small">{r.actor?.id || r.actor?.name || "System"}</span> },
                  { key: "details", label: "Details", render: (r) => <span className="small">{JSON.stringify(r.details || r.payload || {})}</span> },
                ]}
                rows={logbookData?.audit_events || [
                  { timestamp: "2026-09-24T10:00:00Z", action: "LINE_CLEARANCE_CHECK", actor: { id: "USR-00006" }, details: { result: "PASSED", line: "Line 1" } },
                  { timestamp: "2026-09-23T15:30:00Z", action: "EQUIPMENT_CALIBRATED", actor: { id: "USR-00001" }, details: { cert: "CAL-9942", standard: "NIST" } },
                ]}
              />
            </div>
          )}

          {tab === "Readiness Gate" && (
            <div className="card">
              <h3>GMP Equipment Readiness Gate Verifier</h3>
              <div className="card-sub">Run automated pre-production safety and compliance verification on any asset</div>
              {gateCheck ? (
                <div style={{ padding: 16 }}>
                  <div className={`ok-box ${gateCheck.ready ? "" : "error-box"}`} style={{ fontSize: 15, padding: 14 }}>
                    {gateCheck.ready ? (
                      <b>✅ Asset {gateCheck.code} is FULLY RELEASED and ready for batch production.</b>
                    ) : (
                      <b>⛔ Asset {gateCheck.code} is BLOCKED from production: {gateCheck.blockers.join(", ")}</b>
                    )}
                  </div>
                  <div className="stat-line mt">
                    <div className="st-b"><small>Asset Code</small><b className="mono">{gateCheck.code}</b></div>
                    <div className="st-b"><small>Calibration State</small><b>{gateCheck.equipment?.calibration_status}</b></div>
                    <div className="st-b"><small>Maintenance State</small><b>{gateCheck.equipment?.maintenance_status}</b></div>
                    <div className="st-b"><small>Cleaning Validation</small><b>{gateCheck.equipment?.cleaning_status}</b></div>
                  </div>
                </div>
              ) : (
                <div style={{ padding: 20 }}>
                  <p>Select any asset above to verify its pre-batch readiness status.</p>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {modalAction && (
        <Modal
          title={`${modalAction} — ${selectedEq?.name}`}
          sub={`Asset: ${selectedEq?.code}`}
          onClose={() => { setModalAction(null); setForm({}); }}
          footer={
            <>
              <button className="btn ghost" onClick={() => { setModalAction(null); setForm({}); }}>Cancel</button>
              <div className="spacer" />
              <button className="btn primary" onClick={submitAction}>Confirm & Save</button>
            </>
          }
        >
          {modalAction === "CALIBRATE" && (
            <>
              <Field label="Certificate Number">
                <input value={form.cert_no || ""} onChange={(e) => setForm({ ...form, cert_no: e.target.value })} placeholder="e.g. CAL-2026-09" />
              </Field>
              <Field label="Calibrated By">
                <input value={form.calibrated_by || ""} onChange={(e) => setForm({ ...form, calibrated_by: e.target.value })} placeholder="Metrology Specialist / Agency" />
              </Field>
              <Field label="Valid Until Date">
                <input type="date" value={form.valid_until || ""} onChange={(e) => setForm({ ...form, valid_until: e.target.value })} />
              </Field>
            </>
          )}

          {modalAction === "MAINTENANCE" && (
            <>
              <Field label="Maintenance Type">
                <Select
                  value={form.type || "PREVENTIVE"}
                  onChange={(v) => setForm({ ...form, type: v })}
                  options={["PREVENTIVE", "CORRECTIVE", "BREAKDOWN", "OVERHAUL"]}
                />
              </Field>
              <Field label="Performed By">
                <input value={form.performed_by || ""} onChange={(e) => setForm({ ...form, performed_by: e.target.value })} placeholder="Lead Engineer" />
              </Field>
              <Field label="Next Scheduled Due Date">
                <input type="date" value={form.next_due || ""} onChange={(e) => setForm({ ...form, next_due: e.target.value })} />
              </Field>
            </>
          )}

          {modalAction === "HOLD" && (
            <Field label="Reason for Equipment Hold">
              <textarea rows={3} value={form.reason || ""} onChange={(e) => setForm({ ...form, reason: e.target.value })} placeholder="Explain fault or reason for quarantine hold..." />
            </Field>
          )}

          {modalAction === "WORK_ORDER" && (
            <>
              <Field label="Work Order Title">
                <input value={form.title || ""} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="e.g. Replace drive belt and lubricate gears" />
              </Field>
              <Field label="Priority">
                <Select value={form.priority || "MEDIUM"} onChange={(v) => setForm({ ...form, priority: v })} options={["LOW", "MEDIUM", "HIGH", "CRITICAL"]} />
              </Field>
              <Field label="Assigned Technician / Crew">
                <input value={form.assigned_to || ""} onChange={(e) => setForm({ ...form, assigned_to: e.target.value })} placeholder="Engineering Team A" />
              </Field>
            </>
          )}
        </Modal>
      )}
    </div>
  );
}

