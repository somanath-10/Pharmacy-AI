import React, { useEffect, useState } from "react";
import { api } from "../api";
import { Topbar } from "../App";
import { Badge, Table, When, useToast, Drawer, Stepper, Field, Kpi, CountTabs, Modal, Timeline } from "../ui";

const STAGES = ["PLANNED", "RELEASED", "DISPENSING", "IN_PROCESS", "PACKAGING",
  "COMPLETED", "FG_QUARANTINE", "QC_COMPLETE", "QA_REVIEW", "BATCH_RELEASED"];

const WASTE_REASONS = ["SCRAP", "SAMPLE", "SPILL", "REWORK_LOSS", "PACKAGING_LOSS",
  "CLEANING_LOSS", "OTHER"];

export default function PlantWorkspace() {
  const [tab, setTab] = useState("Production Orders");
  const [orders, setOrders] = useState([]);
  const [plans, setPlans] = useState([]);
  const [equipment, setEquipment] = useState([]);
  const [boms, setBoms] = useState([]);
  const [anomalies, setAnomalies] = useState(null);
  const [err, setErr] = useState("");
  const [sel, setSel] = useState(null);
  const [trace, setTrace] = useState(null);
  const [traceId, setTraceId] = useState("");
  const [wasteFor, setWasteFor] = useState(null);
  const [wasteForm, setWasteForm] = useState({ quantity: "", reason: "SCRAP", note: "" });
  const [packFor, setPackFor] = useState(null);
  const [packForm, setPackForm] = useState({ pack_size: "", packs_produced: "", label_code: "" });
  const [planForm, setPlanForm] = useState({ product_id: "", quantity: 500 });
  const { toast, toastHost } = useToast();

  const load = async () => {
    try {
      const [o, p, e, b, an] = await Promise.all([
        api("/api/production/orders"), api("/api/production/plans").catch(() => []),
        api("/api/masters/equipment").catch(() => []),
        api("/api/masters/boms").catch(() => []),
        api("/api/production/yield-anomalies").catch(() => null),
      ]);
      setOrders(Array.isArray(o) ? o : o.items || []);
      setPlans(Array.isArray(p) ? p : p.items || []);
      setEquipment(Array.isArray(e) ? e : e.items || []);
      setBoms(Array.isArray(b) ? b : b.items || []);
      setAnomalies(an);
      setErr("");
    } catch (e) { setErr(e.message); }
  };
  useEffect(() => { load(); const t = setInterval(load, 15000); return () => clearInterval(t); }, []);

  const act = async (path, body = {}, label = "Done") => {
    try { await api(path, { method: "POST", body }); toast(label, "ok"); load(); }
    catch (e) { toast(e.message, "err"); }
  };

  const create = async (e) => {
    e.preventDefault();
    act("/api/production/orders", { product_id: planForm.product_id, batch_size: Number(planForm.quantity) },
        "Production order created (PLANNED)");
  };
  const createPlan = async (e) => {
    e.preventDefault();
    act("/api/production/plans", { items: [{ product_id: planForm.product_id, quantity: Number(planForm.quantity) }] },
        "Plan evaluated — check feasibility");
  };

  const runTrace = async () => {
    if (!traceId.trim()) return;
    try { setTrace(await api(`/api/production/trace/${encodeURIComponent(traceId.trim())}`)); }
    catch (e) { toast(e.message, "err"); }
  };

  const openOrders = orders.filter((o) => !["BATCH_RELEASED", "REJECTED", "CANCELLED"].includes(o.status));
  const equipAttention = equipment.filter((e) =>
    e.calibration_status !== "VALID" || (e.status && e.status !== "ACTIVE")).length;

  return (
    <div>
      {toastHost}
      <Topbar title="Plant / Production (MES)"
              sub="Demand → plan → order → BOM → reservation → issue → eBMR → FG → QC → QA release → warehouse" />
      {err && <div className="error-box mb">{err}</div>}

      <div className="grid kpi-4 mb">
        <Kpi ico="🏭" label="Active batches" value={openOrders.length} tone="blue" />
        <Kpi ico="⚠️" label="Equipment attention" value={equipAttention} tone={equipAttention ? "orange" : "green"} />
        <Kpi ico="📉" label="Yield anomalies" value={anomalies ? anomalies.flagged.length : "—"}
             tone={anomalies && anomalies.flagged.length ? "red" : "green"} />
        <Kpi ico="🗓️" label="Production plans" value={plans.length} tone="purple" />
      </div>

      <div className="row mb wrap">
        <CountTabs tabs={[
          { label: "Production Orders", count: openOrders.length },
          { label: "Plans", count: plans.length },
          { label: "Traceability", count: null },
          { label: "Equipment", count: equipAttention },
        ]} active={tab} onChange={setTab} />
        <div className="spacer" />
        <button className="btn ghost" onClick={() =>
          act("/api/agents/run/plant-agent", { goal: "production_planning" }, "Plant agent run started")}>
          🤖 Run Plant Agent
        </button>
      </div>

      <div className="tab-panel" key={tab}>
        {tab === "Production Orders" && (
          <div className="grid" style={{ gridTemplateColumns: "1.7fr 1fr" }}>
            <div className="card rise">
              <h3>Production orders</h3>
              <div className="card-sub">Equipment + material + QA gates block start. Click a row to execute the batch.</div>
              <Table rows={orders} onRow={setSel} empty="No production orders"
                columns={[
                  { key: "order_id", label: "Order", render: (r) => <span className="mono">{r.order_id}</span> },
                  { key: "product_id", label: "Product", render: (r) => <span className="mono">{r.product_id}</span> },
                  { key: "batch_id", label: "Batch", render: (r) => <span className="mono">{r.batch_id}</span> },
                  { key: "batch_size", label: "Size" },
                  { key: "yield_pct", label: "Yield", render: (r) => r.yield_pct != null
                      ? <span className={`badge ${r.yield_pct < (r.min_yield_pct || 95) ? "red" : "green"}`}>{r.yield_pct}%</span>
                      : <span className="muted">—</span> },
                  { key: "waste", label: "Waste", render: (r) => (r.waste_log || []).length
                      ? <span className="badge orange">{(r.waste_log || []).length}</span> : <span className="muted">—</span> },
                  { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                  { key: "open", label: "", render: () => <span className="link small">Open →</span> },
                ]} />
            </div>
            <div>
              <div className="card mb rise d1">
                <h3>New order / plan</h3>
                <form onSubmit={create}>
                  <Field label="Product SKU">
                    <input required className="mono" value={planForm.product_id}
                           onChange={(e) => setPlanForm({ ...planForm, product_id: e.target.value })}
                           placeholder={boms[0]?.product_id || "PRD-00001"} />
                  </Field>
                  <Field label="Batch size">
                    <input type="number" min="1" value={planForm.quantity}
                           onChange={(e) => setPlanForm({ ...planForm, quantity: e.target.value })} />
                  </Field>
                  <div className="row">
                    <button className="btn primary" style={{ flex: 1 }}>Create order</button>
                    <button type="button" className="btn ghost" style={{ flex: 1 }} onClick={createPlan}>
                      Evaluate as plan
                    </button>
                  </div>
                </form>
              </div>
              <div className="card rise d2">
                <h3>Equipment readiness</h3>
                <div className="card-sub">Expired calibration blocks use regardless of flags</div>
                <Table rows={equipment} empty="No equipment"
                  columns={[
                    { key: "code", label: "Code", render: (r) => <span className="mono">{r.code}</span> },
                    { key: "calibration_status", label: "Calib", render: (r) => <Badge>{r.calibration_status}</Badge> },
                    { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                  ]} />
              </div>
            </div>
          </div>
        )}

        {tab === "Plans" && (
          <div className="grid cols-2">
            {plans.map((p) => (
              <div className="card" key={p.plan_id}>
                <div className="row">
                  <h3>{p.name}</h3>
                  <div className="spacer" />
                  <Badge>{p.status}</Badge>
                </div>
                <div className="card-sub mono">{p.plan_id} · cut: {(p.orders_cut || []).join(", ") || "none"}</div>
                <Table rows={p.items} empty="No items"
                  columns={[
                    { key: "product_id", label: "SKU", render: (r) => <span className="mono">{r.product_id}</span> },
                    { key: "quantity", label: "Qty" },
                    { key: "feasible_now", label: "Feasible", render: (r) => r.feasible_now
                        ? <span className="badge green">yes</span> : <span className="badge orange">shortage</span> },
                    { key: "act", label: "", render: (r) => !r.feasible_now ? null : (
                      <button className="btn primary sm" onClick={() =>
                        act(`/api/production/plans/${p.plan_id}/cut/${r.product_id}`, {},
                            "Production order cut from plan")}>Cut order</button>
                    )},
                  ]} />
              </div>
            ))}
            {plans.length === 0 && (
              <div className="card"><div className="empty">No plans yet — create one from the Production Orders tab.</div></div>
            )}
          </div>
        )}

        {tab === "Traceability" && (
          <div className="card">
            <h3>Batch traceability (recall-ready)</h3>
            <div className="card-sub">Vendor lot → raw material → production batch → warehouse → customer, both directions</div>
            <div className="row mb wrap">
              <input className="input mono" style={{ width: 220 }} placeholder="Batch id e.g. B-00001"
                     value={traceId} onChange={(e) => setTraceId(e.target.value)} />
              <button className="btn primary" onClick={runTrace} disabled={!traceId.trim()}>Trace</button>
            </div>
            {trace && (
              <div className="tab-panel">
                <div className="row mb">
                  <Badge>{trace.qa_status || "UNKNOWN"}</Badge>
                  <span className="small muted">{trace.product_id} · order {trace.production_order_id || "—"}</span>
                </div>
                <div className="grid cols-2">
                  <div>
                    <div className="section-title">Backward — raw material lots</div>
                    <Table rows={trace.backward.raw_material_lots} empty="No material issues recorded"
                      columns={[
                        { key: "sku", label: "Material", render: (r) => <span className="mono">{r.sku}</span> },
                        { key: "lot", label: "Lot", render: (r) => <span className="mono">{r.lot || "—"}</span> },
                        { key: "quantity", label: "Qty" },
                        { key: "vendor", label: "Vendor", render: (r) => r.vendor
                            ? <span className="small">{r.vendor.name || r.vendor.vendor_id}</span> : "—" },
                        { key: "grn_id", label: "GRN", render: (r) => <span className="mono small">{r.grn_id || "—"}</span> },
                      ]} />
                  </div>
                  <div>
                    <div className="section-title">Forward — where the batch went</div>
                    <Table rows={trace.forward.dispatched} empty="Not yet dispatched anywhere"
                      columns={[
                        { key: "to", label: "Flow", render: (r) => <Badge>{r.to}</Badge> },
                        { key: "quantity", label: "Qty" },
                        { key: "reference_id", label: "Ref", render: (r) => <span className="mono small">{r.reference_id || "—"}</span> },
                        { key: "customer_id", label: "Customer", render: (r) => r.customer_id
                            ? <span className="mono small">{r.customer_id}</span> : r.patient_ref || "—" },
                      ]} />
                  </div>
                </div>
                <div className="section-title mt">Current stock positions</div>
                <Table rows={trace.current_positions} empty="No open stock of this batch"
                  columns={[
                    { key: "warehouse_id", label: "WH" },
                    { key: "batch_id", label: "Batch", render: (r) => <span className="mono">{r.batch_id || "—"}</span> },
                    { key: "stock_status", label: "Status", render: (r) => <Badge>{r.stock_status}</Badge> },
                    { key: "quantity", label: "Qty" },
                    { key: "uom", label: "UOM" },
                  ]} />
              </div>
            )}
          </div>
        )}

        {tab === "Equipment" && (
          <div className="card">
            <h3>Equipment master</h3>
            <div className="card-sub">Date-deterministic gates: calibration / maintenance / qualification / hold</div>
            <Table rows={equipment} empty="No equipment registered"
              columns={[
                { key: "code", label: "Code", render: (r) => <span className="mono">{r.code}</span> },
                { key: "name", label: "Name" },
                { key: "site_id", label: "Site" },
                { key: "calibration_due", label: "Calib due" },
                { key: "calibration_status", label: "Calib", render: (r) => <Badge>{r.calibration_status}</Badge> },
                { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
              ]} />
          </div>
        )}
      </div>

      {/* eBMR execution drawer */}
      <Drawer title={sel ? `${sel.order_id} — eBMR` : ""} sub="Electronic batch record · append-only"
              open={!!sel} onClose={() => setSel(null)} wide>
        {sel && <EbmrBody sel={sel} act={act} onWaste={() => { setWasteFor(sel); setWasteForm({ quantity: "", reason: "SCRAP", note: "" }); }}
                          onPack={() => { setPackFor(sel); setPackForm({ pack_size: "", packs_produced: "", label_code: "" }); }} />}
      </Drawer>

      {/* waste modal */}
      <Modal title="Record production waste" sub={wasteFor ? `${wasteFor.order_id} · ${wasteFor.product_id}` : ""}
             open={!!wasteFor} onClose={() => setWasteFor(null)}
             footer={
               <>
                 <button className="btn ghost" onClick={() => setWasteFor(null)}>Cancel</button>
                 <div className="spacer" />
                 <button className="btn reject" disabled={!wasteForm.quantity}
                         onClick={() => {
                           act(`/api/production/orders/${wasteFor.order_id}/waste`,
                               { quantity: Number(wasteForm.quantity), reason: wasteForm.reason, note: wasteForm.note },
                               "Waste recorded in eBMR");
                           setWasteFor(null);
                         }}>Record waste</button>
               </>
             }>
        <div className="form-row">
          <Field label="Quantity">
            <input type="number" min="0.001" step="0.001" value={wasteForm.quantity}
                   onChange={(e) => setWasteForm({ ...wasteForm, quantity: e.target.value })} />
          </Field>
          <Field label="Reason code">
            <select value={wasteForm.reason}
                    onChange={(e) => setWasteForm({ ...wasteForm, reason: e.target.value })}>
              {WASTE_REASONS.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
          </Field>
        </div>
        <Field label="Note">
          <textarea rows={2} value={wasteForm.note} placeholder="What happened?"
                    onChange={(e) => setWasteForm({ ...wasteForm, note: e.target.value })} />
        </Field>
      </Modal>

      {/* packing record modal */}
      <Modal title="Batch packing record" sub={packFor ? `${packFor.order_id} · batch ${packFor.batch_id}` : ""}
             open={!!packFor} onClose={() => setPackFor(null)}
             footer={
               <>
                 <button className="btn ghost" onClick={() => setPackFor(null)}>Cancel</button>
                 <div className="spacer" />
                 <button className="btn primary" disabled={!packForm.packs_produced}
                         onClick={() => {
                           act(`/api/production/orders/${packFor.order_id}/packing`,
                               { pack_size: packForm.pack_size,
                                 packs_produced: Number(packForm.packs_produced),
                                 label_code: packForm.label_code },
                               "Packing record appended");
                           setPackFor(null);
                         }}>Append record</button>
               </>
             }>
        <div className="form-row">
          <Field label="Pack size">
            <input value={packForm.pack_size} placeholder="e.g. 10x10 TAB"
                   onChange={(e) => setPackForm({ ...packForm, pack_size: e.target.value })} />
          </Field>
          <Field label="Packs produced">
            <input type="number" min="1" value={packForm.packs_produced}
                   onChange={(e) => setPackForm({ ...packForm, packs_produced: e.target.value })} />
          </Field>
        </div>
        <Field label="Label code / batch number">
          <input className="mono" value={packForm.label_code} placeholder="LBL-..."
                 onChange={(e) => setPackForm({ ...packForm, label_code: e.target.value })} />
        </Field>
      </Modal>
    </div>
  );
}

function EbmrBody({ sel, act, onWaste, onPack }) {
  return (
    <div>
      <div className="row mb">
        <Badge>{sel.status}</Badge>
        <span className="small muted">{sel.product_id} · batch {sel.batch_id} · BOM v{sel.bom_version}</span>
        <div className="spacer" />
        {sel.yield_pct != null && <span className={`badge ${sel.yield_pct < (sel.min_yield_pct || 95) ? "red" : "green"}`}>yield {sel.yield_pct}%</span>}
      </div>
      <div className="section-title">Stage</div>
      <Stepper steps={STAGES} current={sel.status} />
      <div className="row mt wrap">
        {sel.status === "PLANNED" &&
          <button className="btn primary sm" onClick={() => act(`/api/production/orders/${sel.order_id}/release`, {}, "Released to shop floor")}>Release</button>}
        {sel.status === "RELEASED" &&
          <button className="btn primary sm" onClick={() => act(`/api/production/orders/${sel.order_id}/reserve-materials`, {}, "Materials reserved (FEFO)")}>Reserve materials</button>}
        {sel.status === "DISPENSING" &&
          <button className="btn primary sm" onClick={() => act(`/api/production/orders/${sel.order_id}/line-clearance`, { checks: { area_clean: true, previous_materials_removed: true, labels_ready: true, documented: true } }, "Line clearance done")}>Line clearance</button>}
        {sel.status === "DISPENSING" &&
          <button className="btn primary sm" onClick={() => act(`/api/production/orders/${sel.order_id}/issue-materials`, {}, "Materials issued to WIP")}>Issue materials</button>}
        {(sel.status === "DISPENSING" || sel.status === "IN_PROCESS") &&
          <button className="btn primary sm" onClick={() => act(`/api/production/orders/${sel.order_id}/start`, {}, "Batch started")}>Start batch</button>}
        {sel.status === "IN_PROCESS" &&
          <button className="btn ghost sm" onClick={onWaste}>Record waste</button>}
        {sel.status === "IN_PROCESS" &&
          <button className="btn primary sm" onClick={() => act(`/api/production/orders/${sel.order_id}/steps`, { step: "GRANULATION", params: { temp_c: 55 } }, "eBMR step recorded")}>Record step</button>}
        {(sel.status === "IN_PROCESS" || sel.status === "PACKAGING") &&
          <button className="btn primary sm" onClick={() => act(`/api/production/orders/${sel.order_id}/complete`, { actual_yield: Math.round(sel.batch_size * 0.99) }, "Batch completed → FG quarantine")}>Complete</button>}
        {sel.status === "PACKAGING" &&
          <button className="btn ghost sm" onClick={onPack}>Packing record</button>}
        {sel.status === "FG_QUARANTINE" &&
          <button className="btn primary sm" onClick={() => act(`/api/production/orders/${sel.order_id}/submit-qc`, {}, "FG QC sample registered")}>Submit FG QC</button>}
        {sel.status === "BATCH_RELEASED" && !sel.fg_released_to_wh &&
          <button className="btn approve sm" onClick={() => act(`/api/production/orders/${sel.order_id}/release-fg`, {}, "FG released to warehouse")}>Release FG to warehouse</button>}
        {sel.status === "BATCH_RELEASED" && sel.fg_released_to_wh &&
          <span className="badge green">FG in warehouse — sellable</span>}
        <div className="spacer" />
        <a className="link small" href={`/workflows?entity=PRODUCTION_ORDER:${sel.order_id}`}>Full workflow →</a>
      </div>

      {(sel.waste_log || []).length > 0 && (
        <>
          <hr className="divider" />
          <div className="section-title">Waste log ({sel.waste_log.length})</div>
          <Table rows={sel.waste_log.map((w, i) => ({ ...w, _i: i }))} empty=""
            columns={[
              { key: "stage", label: "Stage" },
              { key: "quantity", label: "Qty" },
              { key: "reason", label: "Reason", render: (r) => <Badge>{r.reason}</Badge> },
              { key: "note", label: "Note", render: (r) => <span className="small">{r.note || "—"}</span> },
              { key: "at", label: "At", render: (r) => <span className="small">{When(r.at)}</span> },
            ]} />
        </>
      )}

      {(sel.packing_records || []).length > 0 && (
        <>
          <hr className="divider" />
          <div className="section-title">Packing records ({sel.packing_records.length})</div>
          <Table rows={sel.packing_records.map((p, i) => ({ ...p, _i: i }))} empty=""
            columns={[
              { key: "pack_size", label: "Pack size" },
              { key: "packs_produced", label: "Packs" },
              { key: "label_code", label: "Label", render: (r) => <span className="mono small">{r.label_code || "—"}</span> },
              { key: "at", label: "At", render: (r) => <span className="small">{When(r.at)}</span> },
            ]} />
        </>
      )}

      {(sel.ebmr_steps || []).length > 0 && (
        <>
          <hr className="divider" />
          <div className="section-title">Electronic batch record (append-only, {sel.ebmr_steps.length} entries)</div>
          <Timeline items={sel.ebmr_steps.map((s) => ({
            title: s.step,
            detail: typeof s.data === "string" ? s.data : JSON.stringify(s.data).slice(0, 120),
            meta: `${When(s.at)} · ${s.actor?.id || "system"}`,
            tone: s.step?.includes("WASTE") ? "red" : s.step?.includes("RELEASE") ? "green" : "",
          }))} />
        </>
      )}
    </div>
  );
}
