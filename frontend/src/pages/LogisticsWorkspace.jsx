import React, { useEffect, useState } from "react";
import { api } from "../api";
import { Topbar } from "../App";
import { Badge, Table, When, useToast, CountTabs, Drawer, Modal, Field, Stepper } from "../ui";

const TABS = ["Outbound Shipments", "Inbound Tracking", "Carriers", "Fleet"];

export default function LogisticsWorkspace() {
  const [tab, setTab] = useState("Outbound Shipments");
  const [shipments, setShipments] = useState([]);
  const [asns, setAsns] = useState([]);
  const [carriers, setCarriers] = useState([]);
  const [vehicles, setVehicles] = useState([]);
  const [err, setErr] = useState("");
  const [sel, setSel] = useState(null);
  const [failModal, setFailModal] = useState(null);
  const [failReason, setFailReason] = useState("CUSTOMER_UNAVAILABLE");
  const [tempModal, setTempModal] = useState(null);
  const [tempVal, setTempVal] = useState("");
  const { toast, toastHost } = useToast();

  const load = async () => {
    try {
      const [s, a, c, v] = await Promise.all([
        api("/api/logistics/shipments"), api("/api/logistics/inbound/asns").catch(() => []),
        api("/api/logistics/carriers").catch(() => []),
        api("/api/logistics/vehicles").catch(() => []),
      ]);
      setShipments(Array.isArray(s) ? s : s.items || []);
      setAsns(Array.isArray(a) ? a : a.items || []);
      setCarriers(Array.isArray(c) ? c : c.items || []);
      setVehicles(Array.isArray(v) ? v : v.items || []);
      setErr("");
    } catch (e) { setErr(e.message); }
  };
  useEffect(() => { load(); const t = setInterval(load, 15000); return () => clearInterval(t); }, []);

  const act = async (path, body = {}, label = "Done") => {
    try { await api(path, { method: "POST", body }); toast(label, "ok"); load(); setSel((s) => s ? { ...s } : s); }
    catch (e) { toast(e.message, "err"); }
  };

  const stages = ["PLANNED", "LOADING", "DISPATCHED", "IN_TRANSIT", "DELIVERED", "CLOSED"];

  return (
    <div>
      {toastHost}
      <Topbar title="Logistics"
              sub="Ready-to-ship → plan → load → dispatch → track → deliver → POD — agents pick carriers & ETAs" />
      {err && <div className="error-box mb">{err}</div>}

      <div className="row mb wrap">
        <CountTabs tabs={TABS.map((t) => ({
          label: t,
          count: t === "Outbound Shipments" ? shipments.filter((s) => s.status !== "CLOSED").length
            : t === "Inbound Tracking" ? asns.filter((a) => a.status === "IN_TRANSIT").length
            : t === "Carriers" ? carriers.length : vehicles.length,
        }))} active={tab} onChange={setTab} />
        <div className="spacer" />
        <button className="btn ghost"
                onClick={() => act("/api/agents/run/logistics-agent", { goal: "route_optimization" }, "Logistics agent run started")}>
          🤖 Run Logistics Agent
        </button>
      </div>

      <div className="tab-panel" key={tab}>
        {tab === "Outbound Shipments" && (
          <div className="card">
            <h3>Outbound shipments</h3>
            <div className="card-sub">Click a shipment for the full lifecycle, exceptions, tracking and POD actions</div>
            <Table rows={shipments} onRow={setSel} empty="No shipments"
              columns={[
                { key: "shipment_id", label: "Shipment", render: (r) => <span className="mono">{r.shipment_id}</span> },
                { key: "sales_order_id", label: "Order", render: (r) => <span className="mono">{r.sales_order_id}</span> },
                { key: "carrier_id", label: "Carrier" },
                { key: "eta", label: "ETA", render: (r) => <span className="small">{When(r.eta)}</span> },
                { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                { key: "exc", label: "Exception", render: (r) => r.delivery_exception
                    ? <span className="badge red">exception</span> : <span className="muted">—</span> },
                { key: "pod", label: "POD", render: (r) => r.pod
                    ? <span className="badge green">captured</span> : <span className="muted">—</span> },
                { key: "open", label: "", render: () => <span className="link small">Open →</span> },
              ]} />
          </div>
        )}

        {tab === "Inbound Tracking" && (
          <div className="card">
            <h3>Inbound tracking</h3>
            <Table rows={asns} empty="No inbound"
              columns={[
                { key: "asn_id", label: "ASN", render: (r) => <span className="mono">{r.asn_id}</span> },
                { key: "po_id", label: "PO", render: (r) => <span className="mono">{r.po_id}</span> },
                { key: "carrier", label: "Carrier" },
                { key: "eta", label: "ETA", render: (r) => <span className="small">{When(r.eta)}</span> },
                { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
              ]} />
          </div>
        )}

        {tab === "Carriers" && (
          <div className="card">
            <h3>Carriers</h3>
            <div className="card-sub">Rating feeds the logistics agent's carrier assignment</div>
            <Table rows={carriers} empty="No carriers"
              columns={[
                { key: "carrier_id", label: "ID", render: (r) => <span className="mono">{r.carrier_id}</span> },
                { key: "name", label: "Carrier" },
                { key: "carrier_type", label: "Mode", render: (r) => <Badge>{r.carrier_type}</Badge> },
                { key: "rating", label: "Rating", render: (r) => <b style={{ color: "var(--green-deep)" }}>{r.rating}</b> },
                { key: "temperature_controlled", label: "Cold chain", render: (r) => r.temperature_controlled
                    ? <span className="badge blue">yes</span> : <span className="muted">—</span> },
              ]} />
          </div>
        )}

        {tab === "Fleet" && (
          <div className="card">
            <h3>Vehicles</h3>
            <div className="card-sub">Fleet master — reefer vehicles feed cold-chain shipment planning</div>
            <Table rows={vehicles} empty="No vehicles registered"
              columns={[
                { key: "vehicle_id", label: "ID", render: (r) => <span className="mono">{r.vehicle_id}</span> },
                { key: "registration", label: "Registration", render: (r) => <span className="mono">{r.registration || r.reg_no || "—"}</span> },
                { key: "vehicle_type", label: "Type", render: (r) => <Badge>{r.vehicle_type}</Badge> },
                { key: "capacity_kg", label: "Capacity kg" },
                { key: "temperature_controlled", label: "Cold chain", render: (r) => r.temperature_controlled
                    ? <span className="badge blue">reefer</span> : <span className="muted">—</span> },
                { key: "status", label: "Status", render: (r) => <Badge>{r.status || "ACTIVE"}</Badge> },
              ]} />
          </div>
        )}
      </div>

      {/* shipment drawer */}
      <Drawer title={sel ? sel.shipment_id : ""} sub="Shipment lifecycle · exceptions · tracking"
              open={!!sel} onClose={() => setSel(null)}>
        {sel && (
          <div>
            <div className="row mb">
              <Badge>{sel.status}</Badge>
              <span className="small muted">{sel.carrier_id || "unassigned carrier"}</span>
            </div>
            <div className="section-title">Lifecycle</div>
            <Stepper steps={stages} current={sel.status} />

            <div className="row mt wrap">
              {["PLANNED", "LOADING"].includes(sel.status) &&
                <button className="btn primary sm" onClick={() =>
                  act(`/api/logistics/shipments/${sel.shipment_id}/dispatch`, {}, "Dispatched — on the road")}>Dispatch</button>}
              {sel.status === "DISPATCHED" &&
                <button className="btn primary sm" onClick={() =>
                  act(`/api/logistics/shipments/${sel.shipment_id}/track`, { event: "IN_TRANSIT" }, "In transit")}>In transit</button>}
              {sel.status === "IN_TRANSIT" &&
                <button className="btn primary sm" onClick={() =>
                  act(`/api/logistics/shipments/${sel.shipment_id}/track`, { event: "DELIVERED" }, "Delivered — sales order updated")}>Mark delivered</button>}
              {["IN_TRANSIT", "DISPATCHED"].includes(sel.status) &&
                <button className="btn reject sm" onClick={() => { setFailModal(sel); setFailReason("CUSTOMER_UNAVAILABLE"); }}>Failed delivery</button>}
              {sel.delivery_exception &&
                <button className="btn approve sm" onClick={() =>
                  act(`/api/logistics/shipments/${sel.shipment_id}/reattempt`, {}, "Reattempt scheduled")}>Schedule reattempt</button>}
              {["IN_TRANSIT", "DISPATCHED"].includes(sel.status) && sel.required_range &&
                <button className="btn ghost sm" onClick={() => { setTempModal(sel); setTempVal(""); }}>Log temperature</button>}
              {sel.status === "DELIVERED" &&
                <button className="btn primary sm" onClick={() =>
                  act(`/api/logistics/shipments/${sel.shipment_id}/pod`, { received_by: "Customer store" }, "POD captured")}>Capture POD</button>}
              <div className="spacer" />
              <a className="link small" href={`/workflows?entity=SHIPMENT:${sel.shipment_id}`}>Full workflow →</a>
            </div>

            {sel.delivery_exception && (
              <div className="error-box mt">
                <span>⚠️</span>
                <span>Delivery exception: {sel.delivery_exception.reason}
                  {sel.next_reattempt_at ? ` — next attempt ${String(sel.next_reattempt_at).slice(0, 16).replace("T", " ")}` : ""}</span>
              </div>
            )}

            {(sel.tracking_events || []).length > 0 && (
              <>
                <hr className="divider" />
                <div className="section-title">Tracking history</div>
                <Table head={["Event", "Location", "At"]}>
                  {sel.tracking_events.map((t, i) => (
                    <tr key={i}><td><span className="badge blue">{t.event}</span></td>
                      <td>{t.location || "—"}</td><td className="small">{When(t.at)}</td></tr>
                  ))}
                </Table>
              </>
            )}
          </div>
        )}
      </Drawer>

      {/* failed delivery modal */}
      <Modal title="Record delivery exception" sub={failModal ? failModal.shipment_id : ""}
             open={!!failModal} onClose={() => setFailModal(null)}
             footer={
               <>
                 <button className="btn ghost" onClick={() => setFailModal(null)}>Cancel</button>
                 <div className="spacer" />
                 <button className="btn reject" onClick={() => {
                   act(`/api/logistics/shipments/${failModal.shipment_id}/track`,
                       { event: "FAILED_DELIVERY", reason: failReason }, "Exception recorded — reattempt scheduled");
                   setFailModal(null);
                 }}>Record exception</button>
               </>
             }>
        <Field label="Exception reason" hint="A reattempt is scheduled automatically per policy.">
          <select value={failReason} onChange={(e) => setFailReason(e.target.value)}>
            <option value="CUSTOMER_UNAVAILABLE">Customer unavailable</option>
            <option value="ADDRESS_ISSUE">Address issue</option>
            <option value="DAMAGED">Damaged in transit</option>
            <option value="REFUSED">Refused by customer</option>
            <option value="WEATHER">Weather / road disruption</option>
          </select>
        </Field>
      </Modal>

      {/* temperature modal */}
      <Modal title="Log transport temperature" sub={tempModal ? `${tempModal.shipment_id} · required ${JSON.stringify(tempModal.required_range)}` : ""}
             open={!!tempModal} onClose={() => setTempModal(null)}
             footer={
               <>
                 <button className="btn ghost" onClick={() => setTempModal(null)}>Cancel</button>
                 <div className="spacer" />
                 <button className="btn primary" disabled={tempVal === ""} onClick={() => {
                   act(`/api/logistics/shipments/${tempModal.shipment_id}/temperature`,
                       { temp: Number(tempVal) }, "Temperature logged (excursion auto-holds)");
                   setTempModal(null);
                 }}>Log reading</button>
               </>
             }>
        <Field label="Logger reading (°C)" hint="An excursion automatically blocks affected batches into QUALITY_HOLD for QA review.">
          <input type="number" step="0.1" value={tempVal} placeholder="e.g. 8.4"
                 onChange={(e) => setTempVal(e.target.value)} />
        </Field>
      </Modal>
    </div>
  );
}
