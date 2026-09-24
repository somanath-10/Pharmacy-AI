import React, { useEffect, useState } from "react";
import { api } from "../api";
import { Topbar } from "../App";
import { Badge, Table, When, useToast, CountTabs, Drawer, Modal, Field, Stepper } from "../ui";

const TABS = ["Inbound (ASN)", "Receiving (GRN)", "Batches", "Availability",
  "Reservations", "Locations", "Transfers", "Cycle Count", "Outbound"];

export default function WarehouseWorkspace() {
  const [tab, setTab] = useState("Receiving (GRN)");
  const [asns, setAsns] = useState([]);
  const [grns, setGrns] = useState([]);
  const [batches, setBatches] = useState([]);
  const [resv, setResv] = useState([]);
  const [products, setProducts] = useState([]);
  const [locations, setLocations] = useState([]);
  const [transfers, setTransfers] = useState([]);
  const [counts, setCounts] = useState([]);
  const [picks, setPicks] = useState([]);
  const [err, setErr] = useState("");
  const [grn, setGrn] = useState(null);
  const [countForm, setCountForm] = useState({ warehouse_id: "WH-MAIN", product_id: "", counted_qty: "" });
  const [transferForm, setTransferForm] = useState({ from_warehouse: "WH-MAIN", to_warehouse: "", product_id: "", quantity: "" });
  const [shortPick, setShortPick] = useState(null);
  const [shortReason, setShortReason] = useState("");
  const [adjust, setAdjust] = useState(null);
  const [adjustReason, setAdjustReason] = useState("");
  const { toast, toastHost } = useToast();

  const load = async () => {
    try {
      const [a, g, b, r, p, loc, trf, cc, pk] = await Promise.all([
        api("/api/logistics/inbound/asns").catch(() => []),
        api("/api/warehouse/grns").catch(() => []),
        api("/api/inventory/batches").catch(() => []),
        api("/api/inventory/reservations").catch(() => []),
        api("/api/masters/products"),
        api("/api/warehouse/locations?warehouse_id=WH-MAIN").catch(() => []),
        api("/api/warehouse/transfers").catch(() => []),
        api("/api/warehouse/cycle-counts").catch(() => []),
        api("/api/warehouse/picks").catch(() => []),
      ]);
      setAsns(Array.isArray(a) ? a : a.items || []);
      setGrns(Array.isArray(g) ? g : g.items || []);
      setBatches(Array.isArray(b) ? b : b.items || []);
      setResv(Array.isArray(r) ? r : r.items || []);
      setProducts(Array.isArray(p) ? p : []);
      setLocations(Array.isArray(loc) ? loc : loc.items || []);
      setTransfers(Array.isArray(trf) ? trf : trf.items || []);
      setCounts(Array.isArray(cc) ? cc : cc.items || []);
      setPicks(Array.isArray(pk) ? pk : pk.items || []);
      setErr("");
    } catch (e) { setErr(e.message); }
  };
  useEffect(() => { load(); const t = setInterval(load, 15000); return () => clearInterval(t); }, []);

  const act = async (path, body = {}, label = "Done") => {
    try { await api(path, { method: "POST", body }); toast(label, "ok"); load(); }
    catch (e) { toast(e.message, "err"); }
  };

  const recordCount = async () => {
    try {
      await api("/api/warehouse/cycle-count", {
        method: "POST",
        body: { warehouse_id: countForm.warehouse_id, product_id: countForm.product_id,
                counted_qty: Number(countForm.counted_qty) },
      });
      toast("Count recorded — variance queued for investigation", "ok");
      setCountForm({ ...countForm, product_id: "", counted_qty: "" });
      load();
    } catch (e) { toast(e.message, "err"); }
  };

  const createTransfer = async () => {
    try {
      await api("/api/warehouse/transfers", {
        method: "POST",
        body: { from_warehouse: transferForm.from_warehouse, to_warehouse: transferForm.to_warehouse,
                lines: [{ sku: transferForm.product_id, quantity: Number(transferForm.quantity), uom: "BOX" }] },
      });
      toast("Transfer requested", "ok");
      setTransferForm({ ...transferForm, to_warehouse: "", product_id: "", quantity: "" });
      load();
    } catch (e) { toast(e.message, "err"); }
  };

  const countsByStatus = (s) => counts.filter((c) => c.status === s).length;
  const openAsns = asns.filter((a) => a.status === "IN_TRANSIT").length;
  const activeRes = resv.filter((r) => r.status === "ACTIVE").length;
  const openTransfers = transfers.filter((t) => t.status !== "CLOSED").length;

  const tabCounts = {
    "Inbound (ASN)": openAsns, "Receiving (GRN)": grns.length, "Batches": batches.length,
    "Availability": products.length, "Reservations": activeRes, "Locations": locations.length,
    "Transfers": openTransfers, "Cycle Count": countsByStatus("VARIANCE_DETECTED"),
    "Outbound": picks.filter((p) => p.status === "PENDING").length,
  };

  return (
    <div>
      {toastHost}
      <Topbar title="Warehouse & Inventory"
              sub="ASN → gate → GRN → quarantine → QC → putaway → FEFO batches → reservations → pick/pack" />
      {err && <div className="error-box mb">{err}</div>}

      <div className="row mb wrap">
        <CountTabs tabs={TABS.map((t) => ({ label: t, count: tabCounts[t] }))} active={tab} onChange={setTab} />
        <div className="spacer" />
        <button className="btn ghost"
                onClick={() => act("/api/agents/run/warehouse-agent", { goal: "receiving_assist" }, "Warehouse agent run started")}>
          🤖 Run Warehouse Agent
        </button>
      </div>

      <div className="tab-panel" key={tab}>
        {tab === "Inbound (ASN)" && (
          <div className="card">
            <h3>Inbound shipments (ASN)</h3>
            <div className="card-sub">Vendor advances shipment info — gate and dock know what's arriving</div>
            <Table rows={asns} empty="No inbound ASNs"
              columns={[
                { key: "asn_id", label: "ASN", render: (r) => <span className="mono">{r.asn_id}</span> },
                { key: "po_id", label: "PO", render: (r) => <span className="mono">{r.po_id}</span> },
                { key: "carrier", label: "Carrier" },
                { key: "eta", label: "ETA", render: (r) => <span className="small">{When(r.eta)}</span> },
                { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
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
            <div className="card-sub">Receipt → batch quarantine → auto QC sampling → disposition → putaway. Click a row for details.</div>
            <Table rows={grns} onRow={setGrn} empty="No GRNs yet"
              columns={[
                { key: "grn_id", label: "GRN", render: (r) => <span className="mono">{r.grn_id}</span> },
                { key: "po_id", label: "PO", render: (r) => <span className="mono">{r.po_id}</span> },
                { key: "warehouse_id", label: "WH" },
                { key: "lines", label: "Lines", render: (r) => (r.lines || []).length },
                { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                { key: "received_at", label: "Received", render: (r) => <span className="small">{When(r.received_at)}</span> },
                { key: "open", label: "", render: () => <span className="link small">Open →</span> },
              ]} />
          </div>
        )}

        {tab === "Batches" && (
          <div className="card">
            <h3>Batch inventory (FEFO)</h3>
            <div className="card-sub">Blocked batches are excluded from availability — quarantine, holds, recalls, expiry</div>
            <div className="row mb">
              <button className="btn ghost sm" onClick={() =>
                act("/api/inventory/expire-sweep", {}, "Expiry sweep complete")}>⏱ Run expiry sweep</button>
            </div>
            <Table rows={batches} empty="No batches"
              columns={[
                { key: "batch_id", label: "Batch", render: (r) => <span className="mono">{r.batch_id}</span> },
                { key: "product_id", label: "Product", render: (r) => <span className="mono">{r.product_id}</span> },
                { key: "expiry_date", label: "Expiry" },
                { key: "qa_status", label: "QA", render: (r) => <Badge>{r.qa_status}</Badge> },
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
                { key: "type", label: "Type", render: (r) => <Badge>{r.type}</Badge> },
                { key: "schedule", label: "Schedule" },
              ]} />
          </div>
        )}

        {tab === "Reservations" && (
          <div className="card">
            <h3>Active reservations</h3>
            <div className="card-sub">Concurrency-safe — two orders can never reserve the same last stock</div>
            <Table rows={resv.filter((r) => r.status === "ACTIVE")} empty="No active reservations"
              columns={[
                { key: "product_id", label: "SKU", render: (r) => <span className="mono">{r.product_id}</span> },
                { key: "quantity", label: "Qty" },
                { key: "reference_type", label: "For" },
                { key: "reference_id", label: "Ref", render: (r) => <span className="mono">{r.reference_id}</span> },
                { key: "created_at", label: "Created", render: (r) => <span className="small">{When(r.created_at)}</span> },
              ]} />
          </div>
        )}

        {tab === "Locations" && (
          <div className="card">
            <h3>Warehouse locations (WH-MAIN)</h3>
            <div className="card-sub">Zone → aisle → rack → shelf → bin. A batch can live in many bins.</div>
            <Table rows={locations} empty="No locations defined"
              columns={[
                { key: "location_id", label: "Bin", render: (r) => <span className="mono">{r.location_id}</span> },
                { key: "zone", label: "Zone", render: (r) => <Badge>{r.zone}</Badge> },
                { key: "aisle", label: "Aisle" },
                { key: "rack", label: "Rack" },
                { key: "shelf", label: "Shelf" },
                { key: "temp_min", label: "Min °C", render: (r) => r.temp_min ?? "—" },
                { key: "temp_max", label: "Max °C", render: (r) => r.temp_max ?? "—" },
                { key: "pick_frequency", label: "Pick freq" },
              ]} />
          </div>
        )}

        {tab === "Transfers" && (
          <div className="card">
            <h3>Stock transfers</h3>
            <div className="card-sub">Requested → approved → picked → dispatched (IN_TRANSIT) → received → closed</div>
            <div className="row mb wrap">
              <input className="input" style={{ width: 110 }} placeholder="From WH"
                     value={transferForm.from_warehouse}
                     onChange={(e) => setTransferForm({ ...transferForm, from_warehouse: e.target.value })} />
              <input className="input" style={{ width: 110 }} placeholder="To WH"
                     value={transferForm.to_warehouse}
                     onChange={(e) => setTransferForm({ ...transferForm, to_warehouse: e.target.value })} />
              <input className="input" style={{ width: 140 }} placeholder="SKU"
                     value={transferForm.product_id}
                     onChange={(e) => setTransferForm({ ...transferForm, product_id: e.target.value })} />
              <input className="input" style={{ width: 90 }} placeholder="Qty" type="number"
                     value={transferForm.quantity}
                     onChange={(e) => setTransferForm({ ...transferForm, quantity: e.target.value })} />
              <button className="btn primary sm" onClick={createTransfer}
                      disabled={!transferForm.to_warehouse || !transferForm.product_id}>Request transfer</button>
            </div>
            <Table rows={transfers} empty="No transfers"
              columns={[
                { key: "transfer_id", label: "Transfer", render: (r) => <span className="mono">{r.transfer_id}</span> },
                { key: "from_warehouse", label: "From" },
                { key: "to_warehouse", label: "To" },
                { key: "lines", label: "Lines", render: (r) => (r.lines || []).length },
                { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                { key: "act", label: "", render: (r) => (
                  <span className="row" style={{ gap: 4 }}>
                    {r.status === "TRANSFER_REQUESTED" &&
                      <button className="btn approve sm" onClick={() =>
                        act(`/api/warehouse/transfers/${r.transfer_id}/approve`, {}, "Approved")}>Approve</button>}
                    {r.status === "APPROVED" &&
                      <button className="btn primary sm" onClick={() =>
                        act(`/api/warehouse/transfers/${r.transfer_id}/pick`, {}, "Picked")}>Pick</button>}
                    {r.status === "PICKED" &&
                      <button className="btn primary sm" onClick={() =>
                        act(`/api/warehouse/transfers/${r.transfer_id}/dispatch`, {}, "Dispatched — in transit")}>Dispatch</button>}
                    {r.status === "DISPATCHED" &&
                      <button className="btn approve sm" onClick={() =>
                        act(`/api/warehouse/transfers/${r.transfer_id}/receive`, {}, "Received — stock available")}>Receive</button>}
                  </span>
                ) },
              ]} />
          </div>
        )}

        {tab === "Cycle Count" && (
          <div className="card">
            <h3>Cycle counting (two-phase)</h3>
            <div className="card-sub">Variance is recorded, never auto-applied — an authorized approver posts the adjustment</div>
            <div className="row mb wrap">
              <input className="input" style={{ width: 110 }} placeholder="Warehouse"
                     value={countForm.warehouse_id}
                     onChange={(e) => setCountForm({ ...countForm, warehouse_id: e.target.value })} />
              <input className="input" style={{ width: 150 }} placeholder="SKU"
                     value={countForm.product_id}
                     onChange={(e) => setCountForm({ ...countForm, product_id: e.target.value })} />
              <input className="input" style={{ width: 90 }} placeholder="Counted" type="number"
                     value={countForm.counted_qty}
                     onChange={(e) => setCountForm({ ...countForm, counted_qty: e.target.value })} />
              <button className="btn primary sm" onClick={recordCount}
                      disabled={!countForm.product_id || countForm.counted_qty === ""}>Record count</button>
            </div>
            <Table rows={counts} empty="No counts recorded"
              columns={[
                { key: "count_id", label: "Count", render: (r) => <span className="mono">{String(r.count_id).slice(-8)}</span> },
                { key: "warehouse_id", label: "WH" },
                { key: "product_id", label: "SKU", render: (r) => <span className="mono">{r.product_id}</span> },
                { key: "system_qty", label: "System" },
                { key: "counted_qty", label: "Counted" },
                { key: "variance", label: "Variance", render: (r) => (
                  <span className={r.variance ? "badge orange" : "badge gray"}>{r.variance}</span>) },
                { key: "count_type", label: "Type", render: (r) => <Badge>{r.count_type}</Badge> },
                { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                { key: "act", label: "", render: (r) =>
                  r.status === "VARIANCE_DETECTED" && !r.adjustment_posted ? (
                  <button className="btn approve sm" onClick={() => setAdjust(r)}>Post adjustment</button>
                ) : null },
              ]} />
          </div>
        )}

        {tab === "Outbound" && (
          <div className="card">
            <h3>Outbound picking</h3>
            <div className="card-sub">System-driven FEFO pick tasks — worker scans, never chooses batches</div>
            <Table rows={picks} empty="No pick tasks"
              columns={[
                { key: "task_id", label: "Task", render: (r) => <span className="mono">{r.task_id}</span> },
                { key: "sales_order_id", label: "Order", render: (r) => <span className="mono">{r.sales_order_id}</span> },
                { key: "product_id", label: "SKU", render: (r) => <span className="mono">{r.product_id}</span> },
                { key: "batch_id", label: "Batch (FEFO)", render: (r) => <span className="mono">{r.batch_id || "—"}</span> },
                { key: "quantity", label: "Qty" },
                { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                { key: "act", label: "", render: (r) => r.status === "PENDING" ? (
                  <span className="row" style={{ gap: 4 }}>
                    <button className="btn primary sm" onClick={() =>
                      act(`/api/warehouse/pick/${r.task_id}/confirm`, { scanned_batch_id: r.batch_id },
                          "Pick confirmed")}>Scan confirm</button>
                    <button className="btn reject sm" onClick={() => { setShortPick(r); setShortReason(""); }}>Short</button>
                  </span>
                ) : null },
              ]} />
          </div>
        )}
      </div>

      {/* GRN detail drawer */}
      <Drawer title={`GRN ${grn?.grn_id || ""}`} sub="Receipt → QC → disposition → putaway"
              open={!!grn} onClose={() => setGrn(null)}>
        {grn && <GrnBody grn={grn} act={act} />}
      </Drawer>

      {/* short pick reason modal */}
      <Modal title="Short pick" sub={shortPick ? `Task ${shortPick.task_id} · ${shortPick.product_id}` : ""}
             open={!!shortPick} onClose={() => setShortPick(null)}
             footer={
               <>
                 <button className="btn ghost" onClick={() => setShortPick(null)}>Cancel</button>
                 <div className="spacer" />
                 <button className="btn reject" disabled={!shortReason.trim()}
                         onClick={() => {
                           act(`/api/warehouse/pick/${shortPick.task_id}/short`, { reason: shortReason.trim() }, "Short pick recorded");
                           setShortPick(null);
                         }}>
                   Record short pick
                 </button>
               </>
             }>
      <Field label="Reason" hint="Recorded in the audit trail; the order will be partially fulfilled or backordered.">
        <textarea rows={3} value={shortReason} placeholder="e.g. damaged cartons found during scan"
                  onChange={(e) => setShortReason(e.target.value)} />
      </Field>
      </Modal>

      {/* adjustment reason modal */}
      <Modal title="Post stock adjustment" sub={adjust ? `Count ${String(adjust.count_id).slice(-8)} · variance ${adjust.variance}` : ""}
             open={!!adjust} onClose={() => setAdjust(null)}
             footer={
               <>
                 <button className="btn ghost" onClick={() => setAdjust(null)}>Cancel</button>
                 <div className="spacer" />
                 <button className="btn approve" disabled={!adjustReason.trim()}
                         onClick={() => {
                           act(`/api/warehouse/cycle-count/${adjust.count_id}/adjust`,
                               { reason: adjustReason.trim(), approved_by: { id: "supervisor", roles: ["SUPER_ADMIN"] } },
                               "Adjustment posted");
                           setAdjust(null);
                         }}>
                   Post adjustment
                 </button>
               </>
             }>
      <Field label="Investigation finding / reason" hint="Required — every adjustment is audited with its approver and reference.">
        <textarea rows={3} value={adjustReason} placeholder="e.g. physical recount confirms 3 units damaged in storage"
                  onChange={(e) => setAdjustReason(e.target.value)} />
      </Field>
      </Modal>
    </div>
  );
}

function GrnBody({ grn, act }) {
  const id = grn.grn_id;
  const allQcDone = (grn.lines || []).every((l) => l.qa_disposition || !l.qc_sample_id);
  return (
    <div>
      <div className="row mb">
        <Badge>{grn.status}</Badge>
        <span className="small muted">received {When(grn.received_at)}</span>
      </div>
      <div className="section-title">Line detail</div>
      <Table head={["Line", "SKU", "Received", "Batch", "QC sample", "Location", "Acc", "Rej"]}>
        {(grn.lines || []).map((l) => (
          <tr key={l.line_no}>
            <td>{l.line_no}</td><td className="mono">{l.sku}</td><td>{l.received_qty}</td>
            <td className="mono">{l.batch_id}</td>
            <td className="mono">{l.qc_sample_id || "—"}</td>
            <td className="mono">{l.location_id || "—"}</td>
            <td>{l.accepted_qty ?? "—"}</td><td>{l.rejected_qty ?? "—"}</td>
          </tr>
        ))}
      </Table>
      <div className="row mt wrap">
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
      </div>
      <div className="row mt">
        {allQcDone && grn.status === "QC_PASSED" &&
          <button className="btn primary" onClick={() =>
            act(`/api/warehouse/grn/${id}/putaway`, {}, "Putaway complete — stock binned")}>Execute putaway</button>}
        <div className="spacer" />
        <a className="link small" href={`/workflows?entity=GRN:${id}`}>Full workflow →</a>
      </div>
      <hr className="divider" />
      <div className="section-title">Putaway flow</div>
      <Stepper steps={["RECEIVED", "QUARANTINE", "QC", "DISPOSITION", "PUTAWAY"]}
               current={grn.status === "QC_PASSED" && allQcDone ? "PUTAWAY" : "QC"} />
    </div>
  );
}
