import React, { useEffect, useState } from "react";
import { api } from "../api";
import { Topbar } from "../App";
import { Badge, Table, When, useToast } from "../ui";

export default function PlantWorkspace() {
  const [orders, setOrders] = useState([]);
  const [equipment, setEquipment] = useState([]);
  const [boms, setBoms] = useState([]);
  const [err, setErr] = useState("");
  const [sel, setSel] = useState(null);
  const [newOrder, setNewOrder] = useState({ product_id: "", batch_size: 500 });
  const toast = useToast();

  const load = async () => {
    try {
      const [o, e, b] = await Promise.all([
        api("/api/production/orders"), api("/api/masters/equipment").catch(() => []),
        api("/api/masters/boms").catch(() => []),
      ]);
      setOrders(Array.isArray(o) ? o : o.items || []);
      setEquipment(Array.isArray(e) ? e : e.items || []);
      setBoms(Array.isArray(b) ? b : b.items || []);
      setErr("");
    } catch (e) { setErr(e.message); }
  };
  useEffect(() => { load(); const t = setInterval(load, 15000); return () => clearInterval(t); }, []);

  const act = async (path, body = {}, label = "Done") => {
    try { await api(path, { method: "POST", body }); toast(label); load(); }
    catch (e) { toast(e.message, "err"); }
  };
  const create = async (e) => {
    e.preventDefault();
    try {
      await api("/api/production/orders", { method: "POST", body: newOrder });
      toast("Production order created (PLANNED)");
      setNewOrder({ product_id: "", batch_size: 500 }); load();
    } catch (ex) { toast(ex.message, "err"); }
  };

  return (
    <div>
      <Topbar title="Plant / Production (MES)"
              sub="BOM → reservation → line clearance → issue → start → eBMR → FG quarantine → QC → QA release" />
      {err && <div className="error-box mb">{err}</div>}
      <div className="grid" style={{ gridTemplateColumns: "1.7fr 1fr" }}>
        <div className="card">
          <h3>Production orders</h3>
          <div className="card-sub">Equipment gates block start when calibration/maintenance/cleaning is not valid</div>
          <Table rows={orders} onRow={setSel} empty="No production orders"
            columns={[
              { key: "order_id", label: "Order", render: (r) => <span className="mono">{r.order_id}</span> },
              { key: "product_id", label: "Product", render: (r) => <span className="mono">{r.product_id}</span> },
              { key: "batch_id", label: "Batch", render: (r) => <span className="mono">{r.batch_id}</span> },
              { key: "batch_size", label: "Size" },
              { key: "yield_pct", label: "Yield", render: (r) => r.yield_pct ? `${r.yield_pct}%` : "—" },
              { key: "status", label: "Status", render: (r) => <Badge value={r.status} /> },
            ]} />
        </div>

        <div>
          <div className="card mb">
            <h3>New production order</h3>
            <form onSubmit={create}>
              <div className="field"><label>Product SKU</label>
                <input required className="mono" value={newOrder.product_id}
                       onChange={(e) => setNewOrder({ ...newOrder, product_id: e.target.value })}
                       placeholder={boms[0]?.product_id || "PRD-00001"} /></div>
              <div className="field"><label>Batch size</label>
                <input type="number" min="1" value={newOrder.batch_size}
                       onChange={(e) => setNewOrder({ ...newOrder, batch_size: Number(e.target.value) })} /></div>
              <button className="btn primary" style={{ width: "100%" }}>Create (PLANNED)</button>
            </form>
          </div>
          <div className="card">
            <h3>Equipment readiness</h3>
            <Table rows={equipment} empty="No equipment"
              columns={[
                { key: "code", label: "Code", render: (r) => <span className="mono">{r.code}</span> },
                { key: "calibration_status", label: "Calib", render: (r) =>
                  <Badge value={r.calibration_status} /> },
                { key: "status", label: "Status", render: (r) => <Badge value={r.status} /> },
              ]} />
          </div>
        </div>
      </div>

      {sel && (
        <div className="card mt">
          <div className="row">
            <h3>{sel.order_id} — eBMR execution</h3>
            <div className="spacer" />
            <Badge value={sel.status} />
            <button className="btn ghost sm" onClick={() => setSel(null)}>Close</button>
          </div>
          <div className="wf-flow mt">
            {["PLANNED", "RELEASED", "DISPENSING", "IN_PROCESS", "PACKAGING", "COMPLETED", "FG_QUARANTINE", "QC_COMPLETE", "QA_REVIEW", "BATCH_RELEASED"].map((s, i) => (
              <React.Fragment key={s}>
                {i > 0 && <span className="wf-arrow">→</span>}
                <div className={`wf-node ${s === sel.status ? "active" : ""}`}><b>{s.replace("_", " ")}</b></div>
              </React.Fragment>
            ))}
          </div>
          <div className="row mt" style={{ flexWrap: "wrap" }}>
            {sel.status === "PLANNED" &&
              <button className="btn primary sm" onClick={() => act(`/api/production/orders/${sel.order_id}/release`, {}, "Released to shop floor")}>Release</button>}
            {sel.status === "RELEASED" &&
              <button className="btn primary sm" onClick={() => act(`/api/production/orders/${sel.order_id}/reserve-materials`, {}, "Materials reserved (FEFO)")}>Reserve materials</button>}
            {sel.status === "DISPENSING" &&
              <button className="btn primary sm" onClick={() => act(`/api/production/orders/${sel.order_id}/line-clearance`, { checks: { area_clean: true, previous_materials_removed: true, labels_ready: true, documented: true } }, "Line clearance done")}>Line clearance</button>}
            {(sel.status === "DISPENSING") &&
              <button className="btn primary sm" onClick={() => act(`/api/production/orders/${sel.order_id}/issue-materials`, {}, "Materials issued to WIP")}>Issue materials</button>}
            {(sel.status === "DISPENSING" || sel.status === "IN_PROCESS") &&
              <button className="btn primary sm" onClick={() => act(`/api/production/orders/${sel.order_id}/start`, {}, "Batch started")}>Start batch</button>}
            {sel.status === "IN_PROCESS" &&
              <button className="btn primary sm" onClick={() => act(`/api/production/orders/${sel.order_id}/steps`, { step: "GRANULATION", params: { temp_c: 55 } }, "eBMR step recorded")}>Record step</button>}
            {(sel.status === "IN_PROCESS" || sel.status === "PACKAGING") &&
              <button className="btn primary sm" onClick={() => act(`/api/production/orders/${sel.order_id}/complete`, { actual_yield: Math.round(sel.batch_size * 0.99) }, "Batch completed → FG quarantine")}>Complete</button>}
            {sel.status === "FG_QUARANTINE" &&
              <button className="btn primary sm" onClick={() => act(`/api/production/orders/${sel.order_id}/submit-qc`, {}, "FG QC sample registered")}>Submit FG QC</button>}
            <div className="spacer" />
            <a className="small" style={{ color: "var(--blue)", fontWeight: 700 }}
               href={`/workflows?entity=PRODUCTION_ORDER:${sel.order_id}`}>Full workflow →</a>
          </div>
          {(sel.ebmr_steps || []).length > 0 && (
            <div className="mt">
              <b className="small">Electronic batch record (append-only)</b>
              <table className="table">
                <thead><tr><th>Step</th><th>Data</th><th>Actor</th><th>At</th></tr></thead>
                <tbody>
                  {sel.ebmr_steps.map((s, i) => (
                    <tr key={i}><td><span className="badge purple">{s.step}</span></td>
                      <td className="mono small">{JSON.stringify(s.data).slice(0, 70)}</td>
                      <td className="small mono">{s.actor?.id}</td><td className="small">{When(s.at)}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
