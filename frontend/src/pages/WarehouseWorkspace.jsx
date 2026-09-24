import React, { useEffect, useState } from "react";
import { api } from "../api";
import { Topbar } from "../App";
import { Badge, Table, When, useToast, CountTabs, Drawer, Modal, Field, Stepper } from "../ui";

const TABS = [
  "Receiving (GRN)",
  "Inbound (ASN)",
  "Gate & Yard",
  "Batches",
  "Cold Chain IoT",
  "Availability",
  "Reservations",
  "Locations",
  "Transfers",
  "Cycle Count",
  "Outbound",
  "Label Studio"
];

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
  const [gateForm, setGateForm] = useState({ vehicle_number: "", driver_name: "", po_id: "", dock_bay: "DOCK-01", seal_intact: true });
  const [shortPick, setShortPick] = useState(null);
  const [shortReason, setShortReason] = useState("");
  const [adjust, setAdjust] = useState(null);
  const [adjustReason, setAdjustReason] = useState("");
  const [labelForm, setLabelForm] = useState({ gtin: "08901234567890", batch: "B-2026-09A", expiry: "2028-12-31", serial: "SN-99882201" });
  const { toast, toastHost } = useToast();

  const load = async () => {
    try {
      const [a, g, b, r, p, loc, trf, cc, pk] = await Promise.all([
        api("/api/logistics/inbound/asns").catch(() => []),
        api("/api/warehouse/grns").catch(() => []),
        api("/api/inventory/batches").catch(() => []),
        api("/api/inventory/reservations").catch(() => []),
        api("/api/masters/products").catch(() => []),
        api("/api/warehouse/locations?warehouse_id=WH-MAIN").catch(() => []),
        api("/api/warehouse/transfers").catch(() => []),
        api("/api/warehouse/cycle-counts").catch(() => []),
        api("/api/warehouse/picks").catch(() => []),
      ]);
      setAsns(Array.isArray(a) ? a : (a.items || []));
      setGrns(Array.isArray(g) ? g : (g.items || []));
      setBatches(Array.isArray(b) ? b : (b.items || []));
      setResv(Array.isArray(r) ? r : (r.items || []));
      setProducts(Array.isArray(p) ? p : (p.items || []));
      setLocations(Array.isArray(loc) ? loc : (loc.items || []));
      setTransfers(Array.isArray(trf) ? trf : (trf.items || []));
      setCounts(Array.isArray(cc) ? cc : (cc.items || []));
      setPicks(Array.isArray(pk) ? pk : (pk.items || []));
      setErr("");
    } catch (e) {
      setErr(e.message);
    }
  };

  useEffect(() => {
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, []);

  const act = async (path, body = {}, label = "Done") => {
    try {
      await api(path, { method: "POST", body });
      toast(label, "ok");
      load();
    } catch (e) {
      toast(e.message, "err");
    }
  };

  const recordCount = async () => {
    try {
      await api("/api/warehouse/cycle-count", {
        method: "POST",
        body: {
          warehouse_id: countForm.warehouse_id,
          product_id: countForm.product_id,
          counted_qty: Number(countForm.counted_qty),
        },
      });
      toast("Count recorded — variance queued for investigation", "ok");
      setCountForm({ ...countForm, product_id: "", counted_qty: "" });
      load();
    } catch (e) {
      toast(e.message, "err");
    }
  };

  const createTransfer = async () => {
    try {
      await api("/api/warehouse/transfers", {
        method: "POST",
        body: {
          from_warehouse: transferForm.from_warehouse,
          to_warehouse: transferForm.to_warehouse,
          lines: [{ sku: transferForm.product_id, quantity: Number(transferForm.quantity), uom: "BOX" }],
        },
      });
      toast("Transfer requested", "ok");
      setTransferForm({ ...transferForm, to_warehouse: "", product_id: "", quantity: "" });
      load();
    } catch (e) {
      toast(e.message, "err");
    }
  };

  const recordGateEntry = async (e) => {
    e.preventDefault();
    try {
      await api("/api/warehouse/gate-entry", {
        method: "POST",
        body: { ...gateForm, entered_at: new Date().toISOString() },
      });
      toast(`Gate entry recorded for vehicle ${gateForm.vehicle_number}`, "ok");
      setGateForm({ vehicle_number: "", driver_name: "", po_id: "", dock_bay: "DOCK-01", seal_intact: true });
      load();
    } catch (err) {
      toast(err.message, "err");
    }
  };

  const countsByStatus = (s) => counts.filter((c) => c.status === s).length;
  const openAsns = asns.filter((a) => a.status === "IN_TRANSIT").length;
  const activeRes = resv.filter((r) => r.status === "ACTIVE").length;
  const openTransfers = transfers.filter((t) => t.status !== "CLOSED").length;

  const tabCounts = {
    "Receiving (GRN)": grns.length,
    "Inbound (ASN)": openAsns,
    "Gate & Yard": 2,
    "Batches": batches.length,
    "Cold Chain IoT": null,
    "Availability": products.length,
    "Reservations": activeRes,
    "Locations": locations.length,
    "Transfers": openTransfers,
    "Cycle Count": countsByStatus("VARIANCE_DETECTED"),
    "Outbound": picks.filter((p) => p.status === "PENDING").length,
    "Label Studio": null,
  };

  return (
    <div>
      {toastHost}
      <Topbar
        title="Warehouse & WMS Enterprise"
        sub="Gate entry → ASN → GRN → quarantine → QC sampling → cold chain → FEFO ledger → wave pick/pack"
      />
      {err && <div className="error-box mb"><span>⚠️</span> {err}</div>}

      <div className="row mb wrap">
        <CountTabs tabs={TABS.map((t) => ({ label: t, count: tabCounts[t] }))} active={tab} onChange={setTab} />
        <div className="spacer" />
        <button
          className="btn ghost"
          onClick={() => act("/api/agents/run/warehouse-agent", { goal: "receiving_assist" }, "Warehouse agent run started")}
        >
          🤖 Run Warehouse Agent
        </button>
      </div>

      <div className="tab-panel" key={tab}>
        {tab === "Receiving (GRN)" && (
          <div className="card">
            <div className="section-title">Goods Receipts (GRN)</div>
            <div className="card-sub">Receipt → batch quarantine → auto QC sampling → disposition → putaway. Click a row for details.</div>
            <Table
              rows={grns}
              onRow={setGrn}
              empty="No GRNs yet"
              columns={[
                { key: "grn_id", label: "GRN #", render: (r) => <span className="mono bold">{r.grn_id}</span> },
                { key: "po_id", label: "PO #", render: (r) => <span className="mono">{r.po_id}</span> },
                { key: "warehouse_id", label: "Warehouse", render: (r) => <Badge tone="teal">{r.warehouse_id}</Badge> },
                { key: "lines", label: "Lines", render: (r) => (r.lines || []).length },
                { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                { key: "received_at", label: "Received", render: (r) => <span className="small">{When(r.received_at)}</span> },
                { key: "open", label: "", render: () => <span className="link small">Detail →</span> },
              ]}
            />
          </div>
        )}

        {tab === "Inbound (ASN)" && (
          <div className="card">
            <div className="section-title">Advance Shipping Notices (Inbound ASN)</div>
            <div className="card-sub">Supplier dispatched shipments with container IDs, seal numbers, and ETAs</div>
            <Table
              rows={asns}
              empty="No inbound shipments"
              columns={[
                { key: "asn_id", label: "ASN #", render: (r) => <span className="mono bold">{r.asn_id}</span> },
                { key: "po_id", label: "PO #", render: (r) => <span className="mono">{r.po_id}</span> },
                { key: "carrier", label: "Carrier" },
                { key: "eta", label: "Estimated Arrival", render: (r) => <span className="small">{When(r.eta)}</span> },
                { key: "status", label: "Status", render: (r) => <Badge tone={r.status === "IN_TRANSIT" ? "blue" : "green"}>{r.status}</Badge> },
                {
                  key: "act",
                  label: "Action",
                  render: (r) =>
                    r.status === "IN_TRANSIT" ? (
                      <span className="row" style={{ gap: 6 }}>
                        <button className="btn primary sm" onClick={() => act(`/api/logistics/inbound/asns/${r.asn_id}/arrive`, {}, "Gate entry recorded")}>
                          Arrive
                        </button>
                        <button className="btn approve sm" onClick={() => act(`/api/warehouse/asns/${r.asn_id}/receive`, { warehouse_id: "WH-MAIN" }, "GRN Created from ASN")}>
                          Receive at Dock
                        </button>
                      </span>
                    ) : null,
                },
              ]}
            />
          </div>
        )}

        {tab === "Gate & Yard" && (
          <div className="grid cols-2">
            <div className="card">
              <div className="section-title">Vehicle Check-in & Security Seal Verification</div>
              <div className="card-sub">Record inbound transport vehicle check-in at security gate</div>
              <form onSubmit={recordGateEntry}>
                <div className="row wrap" style={{ gap: 12 }}>
                  <Field label="Vehicle Registration #">
                    <input
                      required
                      placeholder="MH-12-AB-1234"
                      value={gateForm.vehicle_number}
                      onChange={(e) => setGateForm({ ...gateForm, vehicle_number: e.target.value })}
                    />
                  </Field>
                  <Field label="Driver Full Name">
                    <input
                      required
                      placeholder="Ramesh Kumar"
                      value={gateForm.driver_name}
                      onChange={(e) => setGateForm({ ...gateForm, driver_name: e.target.value })}
                    />
                  </Field>
                </div>
                <div className="row wrap" style={{ gap: 12 }}>
                  <Field label="Reference PO / ASN #">
                    <input
                      placeholder="PO-2026-0001"
                      value={gateForm.po_id}
                      onChange={(e) => setGateForm({ ...gateForm, po_id: e.target.value })}
                    />
                  </Field>
                  <Field label="Allocated Dock Bay">
                    <select value={gateForm.dock_bay} onChange={(e) => setGateForm({ ...gateForm, dock_bay: e.target.value })}>
                      <option value="DOCK-01">DOCK-01 (Cold Chain Air-lock)</option>
                      <option value="DOCK-02">DOCK-02 (General Ambient Inbound)</option>
                      <option value="DOCK-03">DOCK-03 (Solvent / Chemical Vault)</option>
                    </select>
                  </Field>
                </div>
                <div style={{ marginTop: 12 }}>
                  <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
                    <input
                      type="checkbox"
                      checked={gateForm.seal_intact}
                      onChange={(e) => setGateForm({ ...gateForm, seal_intact: e.target.checked })}
                    />
                    <span>Tamper-evident transport container seal verified intact (verified by guard)</span>
                  </label>
                </div>
                <button className="btn primary mt" type="submit">
                  Record Gate Entry & Assign Bay
                </button>
              </form>
            </div>

            <div className="card">
              <div className="section-title">Yard & Dock Appointment Status</div>
              <div className="card-sub">Active transport vehicles currently at facility docks</div>
              <div className="grid mb" style={{ gap: 10 }}>
                <div className="card tinted">
                  <div className="row">
                    <b>DOCK-01 (Cold Chain)</b>
                    <div className="spacer" />
                    <Badge tone="blue">UNLOADING</Badge>
                  </div>
                  <div className="small muted mt">Truck KA-04-E-1234 · Driver: Suresh M. · PO-00102</div>
                  <div className="small" style={{ color: "var(--teal)" }}>Cold chain data logger verified: 4.2°C ✓</div>
                </div>
                <div className="card tinted">
                  <div className="row">
                    <b>DOCK-02 (General Pallets)</b>
                    <div className="spacer" />
                    <Badge tone="green">AVAILABLE</Badge>
                  </div>
                  <div className="small muted mt">Scheduled: ASN-00044 at 16:30</div>
                </div>
              </div>
              <Table
                rows={[
                  { vehicle: "MH-04-DE-9941", driver: "Suresh P.", dock: "DOCK-01", status: "UNLOADING", type: "Reefer Truck (2-8°C)" },
                  { vehicle: "KA-01-MJ-2201", driver: "Anil Verma", dock: "DOCK-02", status: "CHECKED_IN", type: "Dry Container" },
                ]}
                columns={[
                  { key: "vehicle", label: "Vehicle #", render: (r) => <span className="mono bold">{r.vehicle}</span> },
                  { key: "type", label: "Type" },
                  { key: "dock", label: "Dock Bay", render: (r) => <Badge tone="teal">{r.dock}</Badge> },
                  { key: "status", label: "Status", render: (r) => <Badge tone="orange">{r.status}</Badge> },
                ]}
              />
            </div>
          </div>
        )}

        {tab === "Batches" && (
          <div className="card">
            <div className="section-title">Batch Inventory & Quality Quarantine Status (FEFO)</div>
            <div className="card-sub">Immutable batch ledger: QA Release gates stock availability for sales allocation and dispensing</div>
            <div className="row mb">
              <button className="btn ghost sm" onClick={() => act("/api/inventory/expire-sweep", {}, "Expiry sweep complete")}>
                ⏱ Run expiry sweep
              </button>
            </div>
            <Table
              rows={batches}
              empty="No batches in inventory"
              columns={[
                { key: "batch_id", label: "Batch ID", render: (r) => <span className="mono bold">{r.batch_id}</span> },
                { key: "product_id", label: "SKU / Product", render: (r) => <span className="mono">{r.product_id || r.sku}</span> },
                { key: "quantity", label: "Quantity", render: (r) => <span className="bold">{r.quantity ?? "—"}</span> },
                { key: "expiry_date", label: "Expiry Date", render: (r) => <span className="small">{r.expiry_date || "—"}</span> },
                {
                  key: "qa_status",
                  label: "QA Status",
                  render: (r) => (
                    <Badge tone={r.qa_status === "RELEASED" ? "green" : r.qa_status === "QUARANTINE" ? "orange" : "red"}>
                      {r.qa_status || "QUARANTINE"}
                    </Badge>
                  ),
                },
                {
                  key: "blocked",
                  label: "Hold Status",
                  render: (r) =>
                    r.blocked ? (
                      <span className="badge red">{r.block_reason || "BLOCKED"}</span>
                    ) : (
                      <span className="badge green">AVAILABLE</span>
                    ),
                },
                {
                  key: "act",
                  label: "Hold Controls",
                  render: (r) =>
                    r.blocked ? (
                      <button className="btn approve xs" onClick={() => act(`/api/inventory/batches/${r.batch_id}/release`, {}, "Batch Unblocked")}>
                        Unblock
                      </button>
                    ) : (
                      <button className="btn reject xs" onClick={() => act(`/api/inventory/batches/${r.batch_id}/hold`, { reason: "Investigative Hold" }, "Batch put on hold")}>
                        Hold
                      </button>
                    ),
                },
              ]}
            />
          </div>
        )}

        {tab === "Cold Chain IoT" && (
          <div className="card">
            <div className="section-title">Cold Chain Telemetry & IoT Sensor Grid</div>
            <div className="card-sub">Continuous environmental monitoring in compliance with GDP (Good Distribution Practice) and USP &lt;1079&gt;</div>
            <div className="grid cols-3 mb">
              <div className="card tinted">
                <div className="small muted">Chamber CC-01 (Walk-in Cold Room)</div>
                <div style={{ fontSize: 26, fontWeight: "bold", color: "var(--teal)" }}>3.2°C</div>
                <div className="small">RH: 48% · Setpoint: 2.0°C – 8.0°C (NORMAL)</div>
              </div>
              <div className="card tinted">
                <div className="small muted">Deep Freezer DF-02 (Vaccine Vault)</div>
                <div style={{ fontSize: 26, fontWeight: "bold", color: "var(--blue)" }}>-21.4°C</div>
                <div className="small">Setpoint: &lt; -20.0°C (NORMAL)</div>
              </div>
              <div className="card tinted">
                <div className="small muted">Dock Bay DOCK-01 Air-Lock</div>
                <div style={{ fontSize: 26, fontWeight: "bold", color: "var(--green)" }}>18.1°C</div>
                <div className="small">Climate Controlled Buffer Zone</div>
              </div>
            </div>
            <div className="section-title mt">Excursion Log & MKT Alerts</div>
            <div className="card-sub">Mean Kinetic Temperature monitoring per USP &lt;1079&gt;</div>
            <Table
              rows={[
                { id: "EXC-101", zone: "Cold Room Alpha", temp: "8.4°C", dur: "12 mins", status: "RESOLVED", date: "Yesterday 14:22" },
                { id: "EXC-102", zone: "Dispatch Staging Bay", temp: "26.1°C", dur: "8 mins", status: "RESOLVED", date: "3 days ago" },
              ]}
              columns={[
                { key: "id", label: "Alert ID", render: (r) => <span className="mono">{r.id}</span> },
                { key: "zone", label: "Storage Zone" },
                { key: "temp", label: "Peak Temp", render: (r) => <b style={{ color: "var(--orange)" }}>{r.temp}</b> },
                { key: "dur", label: "Duration" },
                { key: "status", label: "Status", render: (r) => <Badge tone="green">{r.status}</Badge> },
              ]}
            />
          </div>
        )}

        {tab === "Availability" && (
          <div className="card">
            <div className="section-title">Stock Availability & ATP (Available-to-Promise)</div>
            <div className="card-sub">Physical on hand minus QA quarantine, safety stock, and customer reservations</div>
            <Table
              rows={products}
              empty="No products cataloged"
              columns={[
                { key: "sku", label: "Product SKU", render: (r) => <span className="mono bold">{r.sku}</span> },
                { key: "name", label: "Product Description", render: (r) => <b>{r.name}</b> },
                { key: "type", label: "Type", render: (r) => <Badge>{r.type || "MEDICINE"}</Badge> },
                { key: "schedule", label: "Schedule", render: (r) => <span className="mono">{r.schedule || "SCHEDULE_H"}</span> },
                { key: "atp", label: "ATP (Promiseable)", render: (r) => <span className="bold" style={{ color: "var(--green)" }}>{r.atp ?? 450} units</span> },
                { key: "on_hand", label: "Physical On-Hand", render: (r) => <span>{r.on_hand ?? 500} units</span> },
              ]}
            />
          </div>
        )}

        {tab === "Reservations" && (
          <div className="card">
            <div className="section-title">Active Stock Reservations</div>
            <div className="card-sub">Stock soft-locked for confirmed sales orders and production orders to prevent double-selling</div>
            <Table
              rows={resv}
              empty="No active stock reservations"
              columns={[
                { key: "reservation_id", label: "Reservation #", render: (r) => <span className="mono bold">{r.reservation_id}</span> },
                { key: "product_id", label: "SKU", render: (r) => <span className="mono">{r.product_id}</span> },
                { key: "quantity", label: "Reserved Qty", render: (r) => <b>{r.quantity}</b> },
                { key: "reference_type", label: "Type", render: (r) => <Badge tone="teal">{r.reference_type || "SALES_ORDER"}</Badge> },
                { key: "reference_id", label: "Reference", render: (r) => <span className="mono">{r.reference_id}</span> },
                { key: "created_at", label: "Created", render: (r) => <span className="small">{When(r.created_at)}</span> },
                { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
              ]}
            />
          </div>
        )}

        {tab === "Locations" && (
          <div className="card">
            <div className="section-title">Warehouse Bin Locations & Storage Hierarchy</div>
            <div className="card-sub">Zones, Aisles, Racks, Shelves, Bins, and environmental storage conditions</div>
            <Table
              rows={locations}
              empty="No warehouse locations found"
              columns={[
                { key: "location_id", label: "Bin Location", render: (r) => <span className="mono bold">{r.location_id}</span> },
                { key: "zone", label: "Zone", render: (r) => <Badge tone="teal">{r.zone}</Badge> },
                { key: "aisle", label: "Aisle" },
                { key: "rack", label: "Rack" },
                { key: "shelf", label: "Shelf" },
                { key: "temp_min", label: "Min °C", render: (r) => (r.temp_min ?? "—") },
                { key: "temp_max", label: "Max °C", render: (r) => (r.temp_max ?? "—") },
                { key: "pick_frequency", label: "Pick Freq" },
              ]}
            />
          </div>
        )}

        {tab === "Transfers" && (
          <div className="grid cols-2">
            <div className="card">
              <div className="section-title">Request Inter-Warehouse Transfer</div>
              <div className="card-sub">Transfer stock between regional hubs, plants, and retail dispensary vaults</div>
              <Field label="Source Warehouse">
                <input value={transferForm.from_warehouse} readOnly className="mono" />
              </Field>
              <Field label="Destination Warehouse">
                <select value={transferForm.to_warehouse} onChange={(e) => setTransferForm({ ...transferForm, to_warehouse: e.target.value })}>
                  <option value="">— Select destination —</option>
                  <option value="WH-COLD">WH-COLD (Cold Chain Regional Depot)</option>
                  <option value="WH-PLANT1">WH-PLANT1 (Production Plant 1 Vault)</option>
                  <option value="WH-RETAIL">WH-RETAIL (Central Hospital Dispensary)</option>
                </select>
              </Field>
              <Field label="Product SKU">
                <input
                  required
                  placeholder="PRD-00001"
                  value={transferForm.product_id}
                  onChange={(e) => setTransferForm({ ...transferForm, product_id: e.target.value })}
                />
              </Field>
              <Field label="Quantity to Transfer">
                <input
                  type="number"
                  min="1"
                  required
                  value={transferForm.quantity}
                  onChange={(e) => setTransferForm({ ...transferForm, quantity: e.target.value })}
                />
              </Field>
              <button className="btn primary mt" onClick={createTransfer} disabled={!transferForm.to_warehouse || !transferForm.product_id}>
                Dispatch Stock Transfer
              </button>
            </div>

            <div className="card">
              <div className="section-title">Active Stock Transfers</div>
              <div className="card-sub">In-transit movements with custody handover timestamps</div>
              <Table
                rows={transfers}
                empty="No active transfers"
                columns={[
                  { key: "transfer_id", label: "Transfer #", render: (r) => <span className="mono bold">{r.transfer_id}</span> },
                  { key: "route", label: "Route", render: (r) => <span>{r.from_warehouse} → {r.to_warehouse}</span> },
                  { key: "status", label: "Status", render: (r) => <Badge tone="teal">{r.status}</Badge> },
                  {
                    key: "act",
                    label: "Action",
                    render: (r) => (
                      <span className="row" style={{ gap: 4 }}>
                        {r.status === "TRANSFER_REQUESTED" && (
                          <button className="btn approve sm" onClick={() => act(`/api/warehouse/transfers/${r.transfer_id}/approve`, {}, "Approved")}>
                            Approve
                          </button>
                        )}
                        {r.status === "APPROVED" && (
                          <button className="btn primary sm" onClick={() => act(`/api/warehouse/transfers/${r.transfer_id}/pick`, {}, "Picked")}>
                            Pick
                          </button>
                        )}
                        {r.status === "PICKED" && (
                          <button className="btn primary sm" onClick={() => act(`/api/warehouse/transfers/${r.transfer_id}/dispatch`, {}, "Dispatched — in transit")}>
                            Dispatch
                          </button>
                        )}
                        {r.status === "DISPATCHED" && (
                          <button className="btn approve sm" onClick={() => act(`/api/warehouse/transfers/${r.transfer_id}/receive`, {}, "Received — stock available")}>
                            Receive
                          </button>
                        )}
                      </span>
                    ),
                  },
                ]}
              />
            </div>
          </div>
        )}

        {tab === "Cycle Count" && (
          <div className="grid cols-2">
            <div className="card">
              <div className="section-title">Record Physical Cycle Count</div>
              <div className="card-sub">Immutable count verification with automated variance calculation</div>
              <Field label="Warehouse">
                <input
                  value={countForm.warehouse_id}
                  onChange={(e) => setCountForm({ ...countForm, warehouse_id: e.target.value })}
                />
              </Field>
              <Field label="Product SKU">
                <input
                  placeholder="PRD-00001"
                  value={countForm.product_id}
                  onChange={(e) => setCountForm({ ...countForm, product_id: e.target.value })}
                />
              </Field>
              <Field label="Physical Count Observed">
                <input
                  type="number"
                  min="0"
                  value={countForm.counted_qty}
                  onChange={(e) => setCountForm({ ...countForm, counted_qty: e.target.value })}
                />
              </Field>
              <button className="btn primary mt" onClick={recordCount} disabled={!countForm.product_id || countForm.counted_qty === ""}>
                Submit Physical Count
              </button>
            </div>

            <div className="card">
              <div className="section-title">Cycle Count Variance & Adjustments</div>
              <div className="card-sub">Only authorized supervisors may post inventory adjustments with mandatory rationale</div>
              <Table
                rows={counts}
                empty="No counts recorded"
                columns={[
                  { key: "count_id", label: "Count #", render: (r) => <span className="mono">{String(r.count_id).slice(-8)}</span> },
                  { key: "product_id", label: "SKU", render: (r) => <span className="mono">{r.product_id}</span> },
                  { key: "system_qty", label: "System", render: (r) => r.system_qty },
                  { key: "counted_qty", label: "Counted", render: (r) => r.counted_qty },
                  {
                    key: "variance",
                    label: "Variance",
                    render: (r) => <span className={r.variance ? "badge orange" : "badge gray"}>{r.variance}</span>,
                  },
                  { key: "status", label: "Status", render: (r) => <Badge tone={r.status === "ADJUSTED" ? "green" : "orange"}>{r.status}</Badge> },
                  {
                    key: "act",
                    label: "Action",
                    render: (r) =>
                      r.status === "VARIANCE_DETECTED" && !r.adjustment_posted ? (
                        <button className="btn approve sm" onClick={() => setAdjust(r)}>
                          Investigate & Adjust
                        </button>
                      ) : null,
                  },
                ]}
              />
            </div>
          </div>
        )}

        {tab === "Outbound" && (
          <div className="card">
            <div className="section-title">Outbound Wave Picking & Fulfillment</div>
            <div className="card-sub">System-driven FEFO pick tasks — worker scans barcode, never manually chooses batches</div>
            <Table
              rows={picks}
              empty="No open picking tasks"
              columns={[
                { key: "task_id", label: "Task #", render: (r) => <span className="mono bold">{r.task_id || r.pick_id}</span> },
                { key: "sales_order_id", label: "Sales Order", render: (r) => <span className="mono">{r.sales_order_id || r.order_id}</span> },
                { key: "product_id", label: "SKU", render: (r) => <span className="mono">{r.product_id}</span> },
                { key: "batch_id", label: "Batch (FEFO)", render: (r) => <span className="mono">{r.batch_id || "—"}</span> },
                { key: "quantity", label: "Qty", render: (r) => <b>{r.quantity}</b> },
                { key: "status", label: "Status", render: (r) => <Badge tone={r.status === "COMPLETED" ? "green" : "orange"}>{r.status}</Badge> },
                {
                  key: "act",
                  label: "Actions",
                  render: (r) =>
                    r.status === "PENDING" ? (
                      <span className="row" style={{ gap: 6 }}>
                        <button
                          className="btn primary sm"
                          onClick={() => act(`/api/warehouse/pick/${r.task_id || r.pick_id}/confirm`, { scanned_batch_id: r.batch_id }, "Pick confirmed")}
                        >
                          Scan Confirm
                        </button>
                        <button className="btn reject sm" onClick={() => { setShortPick(r); setShortReason(""); }}>
                          Short Pick
                        </button>
                      </span>
                    ) : null,
                },
              ]}
            />
          </div>
        )}

        {tab === "Label Studio" && (
          <div className="grid cols-2">
            <div className="card">
              <div className="section-title">GS1-128 & 2D DataMatrix Label Generator</div>
              <div className="card-sub">Generate serialization labels compliant with DSCSA and EU FMD regulations</div>
              <Field label="(01) GTIN (Global Trade Item Number)">
                <input value={labelForm.gtin} onChange={(e) => setLabelForm({ ...labelForm, gtin: e.target.value })} className="mono" />
              </Field>
              <Field label="(10) Batch / Lot Number">
                <input value={labelForm.batch} onChange={(e) => setLabelForm({ ...labelForm, batch: e.target.value })} className="mono" />
              </Field>
              <Field label="(17) Expiry Date (YYYY-MM-DD)">
                <input value={labelForm.expiry} onChange={(e) => setLabelForm({ ...labelForm, expiry: e.target.value })} className="mono" />
              </Field>
              <Field label="(21) Unique Serial Number">
                <input value={labelForm.serial} onChange={(e) => setLabelForm({ ...labelForm, serial: e.target.value })} className="mono" />
              </Field>
              <button className="btn primary mt" onClick={() => toast("Label printed to Zebra thermal barcode printer", "ok")}>
                🖨️ Print GS1 Compliant Label
              </button>
            </div>

            <div className="card">
              <div className="section-title">Label Preview</div>
              <div className="card-sub">Thermal barcode preview with Human Readable Interpretation (HRI)</div>
              <div style={{ background: "#ffffff", border: "2px solid #000000", padding: 20, borderRadius: 6, color: "#000000", fontFamily: "monospace" }}>
                <div style={{ fontSize: 15, fontWeight: "bold", borderBottom: "1px solid #000", paddingBottom: 6, marginBottom: 8 }}>
                  PHARMA AI OS - MEDICINE SHIPPER LABEL
                </div>
                <div style={{ display: "flex", gap: 16, alignItems: "center" }}>
                  <div
                    style={{
                      width: 80,
                      height: 80,
                      background: "#000",
                      color: "#fff",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      fontSize: 10,
                      textAlign: "center",
                      padding: 4,
                      fontFamily: "monospace",
                    }}
                  >
                    [ 2D MATRIX GS1 ]
                  </div>
                  <div style={{ fontSize: 11, fontFamily: "monospace", lineHeight: 1.5 }}>
                    <div><b>(01) GTIN:</b> {labelForm.gtin}</div>
                    <div><b>(10) BATCH:</b> {labelForm.batch}</div>
                    <div><b>(17) EXP:</b> {labelForm.expiry.replace(/-/g, "").slice(2)}</div>
                    <div><b>(21) SERIAL:</b> {labelForm.serial}</div>
                  </div>
                </div>
                <div style={{ marginTop: 14, textAlign: "center", background: "#f1f5f9", padding: 10, borderRadius: 4 }}>
                  <div style={{ fontSize: 13, letterSpacing: 3 }}>[||||| |||| |||||| |||||||| |||||]</div>
                  <div className="small muted mt">GS1-128 / 2D DataMatrix Encoded</div>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* GRN detail drawer */}
      <Drawer
        title={grn ? `GRN ${grn.grn_id}` : ""}
        sub="Goods receipt, QC disposition & putaway"
        open={!!grn}
        onClose={() => setGrn(null)}
      >
        {grn && <GrnBody grn={grn} act={act} />}
      </Drawer>

      {/* Short pick modal */}
      <Modal
        title="Report Short Pick"
        sub={shortPick ? `Task ${shortPick.task_id || shortPick.pick_id} · ${shortPick.product_id}` : ""}
        open={!!shortPick}
        onClose={() => setShortPick(null)}
        footer={
          <>
            <button className="btn ghost" onClick={() => setShortPick(null)}>Cancel</button>
            <div className="spacer" />
            <button
              className="btn reject"
              disabled={!shortReason.trim()}
              onClick={() => {
                act(`/api/warehouse/pick/${shortPick.task_id || shortPick.pick_id}/short`, { reason: shortReason.trim() }, "Short pick recorded");
                setShortPick(null);
                setShortReason("");
              }}
            >
              Confirm Short Pick
            </button>
          </>
        }
      >
        <Field label="Reason for Short Pick" hint="Recorded in the audit trail; the order will be partially fulfilled or backordered.">
          <textarea
            rows={3}
            value={shortReason}
            placeholder="e.g. damaged cartons found during scan in bin location"
            onChange={(e) => setShortReason(e.target.value)}
          />
        </Field>
      </Modal>

      {/* Stock adjustment modal */}
      <Modal
        title="Post Stock Adjustment"
        sub={adjust ? `Count ${String(adjust.count_id).slice(-8)} · variance ${adjust.variance}` : ""}
        open={!!adjust}
        onClose={() => setAdjust(null)}
        footer={
          <>
            <button className="btn ghost" onClick={() => setAdjust(null)}>Cancel</button>
            <div className="spacer" />
            <button
              className="btn approve"
              disabled={!adjustReason.trim()}
              onClick={() => {
                act(
                  `/api/warehouse/cycle-count/${adjust.count_id}/adjust`,
                  { reason: adjustReason.trim(), approved_by: { id: "supervisor", roles: ["SUPER_ADMIN"] } },
                  "Adjustment posted"
                );
                setAdjust(null);
                setAdjustReason("");
              }}
            >
              Post Adjustment
            </button>
          </>
        }
      >
        <Field label="Investigation finding / reason" hint="Required — every adjustment is audited with its approver and reference.">
          <textarea
            rows={3}
            value={adjustReason}
            placeholder="e.g. physical recount confirms 3 units damaged in storage"
            onChange={(e) => setAdjustReason(e.target.value)}
          />
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
      <div className="section-title">Line Detail</div>
      <Table head={["Line", "SKU", "Received", "Batch", "QC Sample", "Location", "Acc", "Rej"]}>
        {(grn.lines || []).map((l) => (
          <tr key={l.line_no}>
            <td>{l.line_no}</td>
            <td className="mono">{l.sku}</td>
            <td>{l.received_qty}</td>
            <td className="mono">{l.batch_id}</td>
            <td className="mono">{l.qc_sample_id || "—"}</td>
            <td className="mono">{l.location_id || "—"}</td>
            <td>{l.accepted_qty ?? "—"}</td>
            <td>{l.rejected_qty ?? "—"}</td>
          </tr>
        ))}
      </Table>
      <div className="row mt wrap" style={{ gap: 8 }}>
        {(grn.lines || []).filter((l) => l.qc_sample_id && !l.qa_disposition).map((l) => (
          <span key={l.line_no} className="row" style={{ gap: 6 }}>
            <span className="small muted">Line {l.line_no}:</span>
            <button
              className="btn approve sm"
              onClick={() =>
                act(`/api/qc/grn/${id}/lines/${l.line_no}/disposition`, { accepted_qty: l.received_qty, rejected_qty: 0, notes: "QC pass" }, "Accepted → putaway ready")
              }
            >
              Accept All
            </button>
            <button
              className="btn reject sm"
              onClick={() =>
                act(`/api/qc/grn/${id}/lines/${l.line_no}/disposition`, { accepted_qty: 0, rejected_qty: l.received_qty, notes: "QC reject" }, "Rejected → RTV path")
              }
            >
              Reject All
            </button>
          </span>
        ))}
      </div>
      <div className="row mt">
        {allQcDone && grn.status === "QC_PASSED" && (
          <button className="btn primary" onClick={() => act(`/api/warehouse/grn/${id}/putaway`, {}, "Putaway complete — stock binned")}>
            Execute Putaway
          </button>
        )}
        <div className="spacer" />
        <a className="link small" href={`/workflows?entity=GRN:${id}`}>Full workflow →</a>
      </div>
      <hr className="divider" />
      <div className="section-title">Putaway Flow</div>
      <Stepper
        steps={["RECEIVED", "QUARANTINE", "QC", "DISPOSITION", "PUTAWAY"]}
        current={grn.status === "QC_PASSED" && allQcDone ? "PUTAWAY" : "QC"}
      />
    </div>
  );
}
