import React, { useEffect, useState } from "react";
import { api } from "../api";
import { Topbar } from "../App";
import { Badge, Table, When, useToast } from "../ui";

const TABS = ["Outbound Shipments", "Inbound Tracking", "Carriers"];

export default function LogisticsWorkspace() {
  const [tab, setTab] = useState("Outbound Shipments");
  const [shipments, setShipments] = useState([]);
  const [asns, setAsns] = useState([]);
  const [carriers, setCarriers] = useState([]);
  const [err, setErr] = useState("");
  const [sel, setSel] = useState(null);
  const toast = useToast();

  const load = async () => {
    try {
      const [s, a, c] = await Promise.all([
        api("/api/logistics/shipments"), api("/api/logistics/inbound/asns").catch(() => []),
        api("/api/logistics/carriers").catch(() => []),
      ]);
      setShipments(Array.isArray(s) ? s : s.items || []);
      setAsns(Array.isArray(a) ? a : a.items || []);
      setCarriers(Array.isArray(c) ? c : c.items || []);
      setErr("");
    } catch (e) { setErr(e.message); }
  };
  useEffect(() => { load(); const t = setInterval(load, 15000); return () => clearInterval(t); }, []);

  const act = async (path, body = {}, label = "Done") => {
    try { await api(path, { method: "POST", body }); toast(label); load(); }
    catch (e) { toast(e.message, "err"); }
  };

  return (
    <div>
      <Topbar title="Logistics"
              sub="Ready-to-ship → plan → load → dispatch → track → deliver → POD — agents pick carriers & ETAs" />
      {err && <div className="error-box mb">{err}</div>}
      <div className="row mb" style={{ flexWrap: "wrap" }}>
        {TABS.map((t) => (
          <button key={t} className={`btn ${tab === t ? "primary" : "ghost"}`} onClick={() => setTab(t)}>{t}</button>
        ))}
        <div className="spacer" />
        <button className="btn ghost"
                onClick={() => act("/api/agents/run/logistics-agent", { goal: "route_optimization" }, "Logistics agent run started")}>
          🤖 Run Logistics Agent
        </button>
      </div>

      {tab === "Outbound Shipments" && (
        <div className="card">
          <h3>Outbound shipments</h3>
          <Table rows={shipments} onRow={setSel} empty="No shipments"
            columns={[
              { key: "shipment_id", label: "Shipment", render: (r) => <span className="mono">{r.shipment_id}</span> },
              { key: "sales_order_id", label: "Order", render: (r) => <span className="mono">{r.sales_order_id}</span> },
              { key: "carrier_id", label: "Carrier" },
              { key: "eta", label: "ETA", render: (r) => When(r.eta) },
              { key: "status", label: "Status", render: (r) => <Badge value={r.status} /> },
              { key: "pod", label: "POD", render: (r) => r.pod ? <span className="badge green">captured</span> : "—" },
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
              { key: "eta", label: "ETA", render: (r) => When(r.eta) },
              { key: "status", label: "Status", render: (r) => <Badge value={r.status} /> },
            ]} />
        </div>
      )}

      {tab === "Carriers" && (
        <div className="card">
          <h3>Carriers</h3>
          <Table rows={carriers} empty="No carriers"
            columns={[
              { key: "carrier_id", label: "ID", render: (r) => <span className="mono">{r.carrier_id}</span> },
              { key: "name", label: "Carrier" },
              { key: "carrier_type", label: "Mode", render: (r) => <Badge value={r.carrier_type} /> },
              { key: "rating", label: "Rating", render: (r) => <b style={{ color: "var(--green)" }}>{r.rating}</b> },
              { key: "temperature_controlled", label: "Cold chain", render: (r) => r.temperature_controlled
                  ? <span className="badge blue">yes</span> : "—" },
            ]} />
        </div>
      )}

      {sel && (
        <div className="card mt">
          <div className="row">
            <h3>{sel.shipment_id}</h3>
            <div className="spacer" />
            <Badge value={sel.status} />
            <button className="btn ghost sm" onClick={() => setSel(null)}>Close</button>
          </div>
          <div className="wf-flow mt">
            {["PLANNED", "LOADING", "DISPATCHED", "IN_TRANSIT", "DELIVERED", "CLOSED"].map((s, i) => (
              <React.Fragment key={s}>
                {i > 0 && <span className="wf-arrow">→</span>}
                <div className={`wf-node ${s === sel.status ? "active" : ""}`}><b>{s}</b></div>
              </React.Fragment>
            ))}
          </div>
          <div className="row mt" style={{ flexWrap: "wrap" }}>
            {["PLANNED", "LOADING"].includes(sel.status) &&
              <button className="btn primary sm" onClick={() =>
                act(`/api/logistics/shipments/${sel.shipment_id}/dispatch`, {}, "Dispatched — on the road")}>Dispatch</button>}
            {sel.status === "DISPATCHED" &&
              <button className="btn primary sm" onClick={() =>
                act(`/api/logistics/shipments/${sel.shipment_id}/track`, { event: "IN_TRANSIT" }, "In transit")}>In transit</button>}
            {sel.status === "IN_TRANSIT" &&
              <button className="btn primary sm" onClick={() =>
                act(`/api/logistics/shipments/${sel.shipment_id}/track`, { event: "DELIVERED" }, "Delivered — sales order updated")}>Mark delivered</button>}
            {sel.status === "DELIVERED" &&
              <button className="btn primary sm" onClick={() =>
                act(`/api/shipments/${sel.shipment_id}/pod`, { received_by: "Customer store" }, "POD captured")}>Capture POD</button>}
            <div className="spacer" />
            <a className="small" style={{ color: "var(--blue)", fontWeight: 700 }}
               href={`/workflows?entity=SHIPMENT:${sel.shipment_id}`}>Full workflow →</a>
          </div>
          {(sel.tracking_events || []).length > 0 && (
            <table className="table mt">
              <thead><tr><th>Event</th><th>Location</th><th>At</th></tr></thead>
              <tbody>
                {sel.tracking_events.map((t, i) => (
                  <tr key={i}><td><span className="badge blue">{t.event}</span></td>
                    <td>{t.location || "—"}</td><td className="small">{When(t.at)}</td></tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}
