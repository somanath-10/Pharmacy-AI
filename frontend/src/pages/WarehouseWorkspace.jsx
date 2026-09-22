import React, { useEffect, useState } from "react";
import { api } from "../api";
import { Topbar } from "../App";
import { Badge, Table, When, useToast } from "../ui";

const TABS = ["Inbound (ASN)", "Receiving (GRN)", "Batches", "Availability", "Reservations"];

export default function WarehouseWorkspace() {
  const [tab, setTab] = useState("Receiving (GRN)");
  const [asns, setAsns] = useState([]);
  const [grns, setGrns] = useState([]);
  const [batches, setBatches] = useState([]);
  const [resv, setResv] = useState([]);
  const [products, setProducts] = useState([]);
  const [err, setErr] = useState("");
  const [grn, setGrn] = useState(null);
  const toast = useToast();

  const load = async () => {
    try {
      const [a, g, b, r, p] = await Promise.all([
        api("/api/logistics/inbound/asns").catch(() => []),
        api("/api/warehouse/grns").catch(() => []),
        api("/api/inventory/batches").catch(() => []),
        api("/api/inventory/reservations").catch(() => []),
        api("/api/masters/products"),
      ]);
      setAsns(Array.isArray(a) ? a : a.items || []);
      setGrns(Array.isArray(g) ? g : g.items || []);
      setBatches(Array.isArray(b) ? b : b.items || []);
      setResv(Array.isArray(r) ? r : r.items || []);
      setProducts(Array.isArray(p) ? p : []);
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
      <Topbar title="Warehouse & Inventory"
              sub="ASN → gate → GRN → quarantine → QC → putaway → FEFO batches → reservations → pick/pack" />
      {err && <div className="error-box mb">{err}</div>}
      <div className="row mb" style={{ flexWrap: "wrap" }}>
        {TABS.map((t) => (
          <button key={t} className={`btn ${tab === t ? "primary" : "ghost"}`} onClick={() => setTab(t)}>{t}</button>
        ))}
        <div className="spacer" />
        <button className="btn ghost"
                onClick={() => act("/api/agents/run/warehouse-agent", { goal: "receiving_assist" }, "Warehouse agent run started")}>
          🤖 Run Warehouse Agent
        </button>
      </div>

      {tab === "Inbound (ASN)" && (
        <div className="card">
          <h3>Inbound shipments (ASN)</h3>
          <div className="card-sub">Vendor advances shipment info — gate and dock know what's arriving</div>
          <Table rows={asns} empty="No inbound ASNs"
            columns={[
              { key: "asn_id", label: "ASN", render: (r) => <span className="mono">{r.asn_id}</span> },
              { key: "po_id", label: "PO", render: (r) => <span className="mono">{r.po_id}</span> },
              { key: "carrier", label: "Carrier" },
              { key: "eta", label: "ETA", render: (r) => When(r.eta) },
              { key: "status", label: "Status", render: (r) => <Badge value={r.status} /> },
              { key: "act", label: "", render: (r) => r.status === "IN_TRANSIT" ? (
                <button className="btn primary sm" onClick={() =>
                  act(`/api/logistics/inbound/asns/${r.asn_id}/arrive`, {}, "Gate entry recorded")}>Arrive</button>
              ) : null },
            ]} />
        </div>
      )}

      {tab === "Receiving (GRN)" && (
        <div className="card">
          <h3>Goods receipts</h3>
          <div className="card-sub">Receipt → batch quarantine → auto QC sampling → disposition → putaway</div>
          <Table rows={grns} onRow={setGrn} empty="No GRNs yet"
            columns={[
              { key: "grn_id", label: "GRN", render: (r) => <span className="mono">{r.grn_id}</span> },
              { key: "po_id", label: "PO", render: (r) => <span className="mono">{r.po_id}</span> },
              { key: "warehouse_id", label: "WH" },
              { key: "lines", label: "Lines", render: (r) => (r.lines || []).length },
              { key: "status", label: "Status", render: (r) => <Badge value={r.status} /> },
              { key: "received_at", label: "Received", render: (r) => When(r.received_at) },
            ]} />
        </div>
      )}

      {tab === "Batches" && (
        <div className="card">
          <h3>Batch inventory (FEFO)</h3>
          <div className="card-sub">Blocked batches are excluded from availability — quarantine, holds, recalls</div>
          <Table rows={batches} empty="No batches"
            columns={[
              { key: "batch_id", label: "Batch", render: (r) => <span className="mono">{r.batch_id}</span> },
              { key: "product_id", label: "Product", render: (r) => <span className="mono">{r.product_id}</span> },
              { key: "expiry_date", label: "Expiry" },
              { key: "qa_status", label: "QA", render: (r) => <Badge value={r.qa_status} /> },
              { key: "blocked", label: "Blocked", render: (r) => r.blocked
                  ? <span className="badge red">{r.block_reason || "blocked"}</span>
                  : <span className="badge green">open</span> },
            ]} />
        </div>
      )}

      {tab === "Availability" && (
        <div className="card">
          <h3>Product availability</h3>
          <div className="card-sub">On-hand minus reservations — ledger-driven, never edited</div>
          <Table rows={products} empty="No products"
            columns={[
              { key: "sku", label: "SKU", render: (r) => <span className="mono">{r.sku}</span> },
              { key: "name", label: "Product" },
              { key: "type", label: "Type", render: (r) => <Badge value={r.type} /> },
              { key: "schedule", label: "Schedule" },
            ]} />
        </div>
      )}

      {tab === "Reservations" && (
        <div className="card">
          <h3>Active reservations</h3>
          <Table rows={resv.filter((r) => r.status === "ACTIVE")} empty="No active reservations"
            columns={[
              { key: "product_id", label: "SKU", render: (r) => <span className="mono">{r.product_id}</span> },
              { key: "quantity", label: "Qty" },
              { key: "reference_type", label: "For" },
              { key: "reference_id", label: "Ref", render: (r) => <span className="mono">{r.reference_id}</span> },
              { key: "created_at", label: "Created", render: (r) => When(r.created_at) },
            ]} />
        </div>
      )}

      {grn && <GrnDetail grn={grn} onClose={() => setGrn(null)} act={act} />}
    </div>
  );
}

function GrnDetail({ grn, onClose, act }) {
  const id = grn.grn_id;
  const allQcDone = (grn.lines || []).every((l) => l.qa_disposition || !l.qc_sample_id);
  return (
    <div className="card mt">
      <div className="row">
        <h3>GRN {id}</h3>
        <div className="spacer" />
        <Badge value={grn.status} />
        <button className="btn ghost sm" onClick={onClose}>Close</button>
      </div>
      <table className="table mt">
        <thead><tr><th>Line</th><th>SKU</th><th>Received</th><th>Batch</th><th>QC sample</th><th>Accepted</th><th>Rejected</th></tr></thead>
        <tbody>
          {(grn.lines || []).map((l) => (
            <tr key={l.line_no}>
              <td>{l.line_no}</td><td className="mono">{l.sku}</td><td>{l.received_qty}</td>
              <td className="mono">{l.batch_id}</td>
              <td className="mono">{l.qc_sample_id || "—"}</td>
              <td>{l.accepted_qty ?? "—"}</td><td>{l.rejected_qty ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <div className="row mt" style={{ flexWrap: "wrap" }}>
        {(grn.lines || []).filter((l) => l.qc_sample_id && !l.qa_disposition).map((l) => (
          <span key={l.line_no} className="row" style={{ gap: 6 }}>
            <span className="small muted">Line {l.line_no}:</span>
            <button className="btn approve sm" onClick={() =>
              act(`/api/qc/grn/${id}/lines/${l.line_no}/disposition`,
                  { accepted_qty: l.received_qty, rejected_qty: 0, notes: "QC pass" },
                  "Accepted → putaway ready")}>Accept all</button>
            <button className="btn reject sm" onClick={() =>
              act(`/api/qc/grn/${id}/lines/${l.line_no}/disposition`,
                  { accepted_qty: 0, rejected_qty: l.received_qty, notes: "QC reject" },
                  "Rejected → RTV path")}>Reject all</button>
          </span>
        ))}
        {allQcDone && grn.status === "QC_PASSED" &&
          <button className="btn primary" onClick={() =>
            act(`/api/warehouse/grn/${id}/putaway`, {}, "Putaway complete — stock available")}>Putaway</button>}
        <div className="spacer" />
        <a className="small" style={{ color: "var(--blue)", fontWeight: 700 }}
           href={`/workflows?entity=GRN:${id}`}>Full workflow →</a>
      </div>
    </div>
  );
}
