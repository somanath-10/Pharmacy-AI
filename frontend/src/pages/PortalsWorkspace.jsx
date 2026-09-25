import React, { useEffect, useState } from "react";
import { api, getMe } from "../api";
import { Topbar } from "../App";
import { Badge, Table, When, useToast, CountTabs, Kpi, Skeleton, Money, Modal, Field } from "../ui";

const TABS = ["Supplier Portal View", "Customer Portal View"];

export default function PortalsWorkspace() {
  const [tab, setTab] = useState("Supplier Portal View");
  const [portalData, setPortalData] = useState({ pos: [], asns: [], invoices: [], orders: [], shipments: [] });
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [showAsn, setShowAsn] = useState(false);
  const [asnForm, setAsnForm] = useState({ po_id: "", carrier: "", vehicle_no: "", driver: "", eta: "" });
  const [showInvoice, setShowInvoice] = useState(false);
  const [invoiceForm, setInvoiceForm] = useState({ po_id: "", supplier_invoice_number: "" });
  const { toast, toastHost } = useToast();
  const roles = getMe()?.roles || [];
  const isSupplier = roles.includes("SUPPLIER");
  const isCustomer = roles.includes("CUSTOMER");
  const portalTabs = TABS.filter((name) =>
    (name === "Supplier Portal View" && isSupplier) ||
    (name === "Customer Portal View" && isCustomer));
  const activeTab = portalTabs.includes(tab) ? tab : portalTabs[0];

  const load = async () => {
    try {
      const [supplier, customer] = await Promise.all([
        isSupplier ? Promise.all([
          api("/api/portal/vendor/pos"),
          api("/api/portal/vendor/asns"),
          api("/api/portal/vendor/invoices"),
        ]) : Promise.resolve([[], [], []]),
        isCustomer ? Promise.all([
          api("/api/portal/customer/orders"),
          api("/api/portal/customer/invoices"),
          api("/api/portal/customer/shipments"),
        ]) : Promise.resolve([[], [], []]),
      ]);
      setPortalData({
        pos: Array.isArray(supplier[0]) ? supplier[0] : [],
        asns: Array.isArray(supplier[1]) ? supplier[1] : [],
        invoices: Array.isArray(supplier[2]) ? supplier[2] : [],
        orders: Array.isArray(customer[0]) ? customer[0] : [],
        shipments: Array.isArray(customer[2]) ? customer[2] : [],
      });
      setErr("");
    } catch (e) {
      setErr(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const ackPo = async (poId) => {
    try {
      await api(`/api/portal/vendor/pos/${poId}/ack`, { method: "POST", body: {} });
      toast(`Purchase Order ${poId} acknowledged`, "ok");
      load();
    } catch (e) {
      toast(e.message, "err");
    }
  };

  const createAsn = async () => {
    const po = portalData.pos.find((item) => item.po_id === asnForm.po_id);
    if (!po) return toast("Choose an open purchase order", "err");
    try {
      await api("/api/portal/vendor/asns", {
        method: "POST",
        body: {
          ...asnForm,
          eta: asnForm.eta ? new Date(asnForm.eta).toISOString() : undefined,
          lines: (po.lines || []).map((line) => ({ line_no: line.line_no, quantity: line.quantity })),
        },
      });
      toast("Advance shipping notice submitted", "ok");
      setShowAsn(false);
      setAsnForm({ po_id: "", carrier: "", vehicle_no: "", driver: "", eta: "" });
      load();
    } catch (e) {
      toast(e.message, "err");
    }
  };

  const submitInvoice = async () => {
    if (!invoiceForm.po_id || !invoiceForm.supplier_invoice_number.trim()) {
      return toast("Purchase order and invoice number are required", "err");
    }
    try {
      await api("/api/portal/vendor/invoices", {
        method: "POST",
        body: { ...invoiceForm, supplier_invoice_number: invoiceForm.supplier_invoice_number.trim() },
      });
      toast("Supplier invoice submitted for matching", "ok");
      setShowInvoice(false);
      setInvoiceForm({ po_id: "", supplier_invoice_number: "" });
      load();
    } catch (e) {
      toast(e.message, "err");
    }
  };

  return (
    <div>
      {toastHost}
      <Topbar
        title="External Portals Collaboration"
        sub="Self-service workspaces for approved external vendors, commercial B2B customers, and authorized third-party regulatory inspectors"
      />

      {err && <div className="error-box mb"><span>⚠️</span> {err}</div>}

      <div className="grid kpi-4 mb">
        <Kpi ico="🤝" label="Open Purchase Orders" value={portalData.pos.length} tone="blue" foot="Supplier portal" />
        <Kpi ico="🛒" label="Pending PO Acknowledgements" value={portalData.pos.filter((p) => p.status === "SENT").length} tone="orange" foot="Requires supplier ack" />
        <Kpi ico="🚚" label="Active ASNs & Shipments" value={portalData.asns.length + portalData.shipments.length} tone="blue" foot="In transit to dock" />
        <Kpi ico="📦" label="My Sales Orders" value={portalData.orders.length} tone="green" foot="Customer portal" />
      </div>

      <div className="row mb wrap">
        <CountTabs
          tabs={portalTabs.map((t) => ({ label: t }))}
          active={activeTab}
          onChange={setTab}
        />
        <div className="spacer" />
      </div>

      {loading ? (
        <Skeleton rows={6} />
      ) : (
        <div className="tab-panel">
          {activeTab === "Supplier Portal View" && (
            <div className="card">
              <div className="row mb">
                <div>
                  <h3>Supplier Self-Service Portal (Acme Corp View)</h3>
                  <div className="card-sub">Vendor portal view for receiving purchase orders, submitting ASNs, and uploading invoices</div>
                </div>
                <div className="spacer" />
                <div className="row" style={{ gap: 8 }}>
                  <button className="btn ghost sm" onClick={() => setShowInvoice(true)}>Submit Invoice</button>
                  <button className="btn primary sm" onClick={() => setShowAsn(true)}>+ Create ASN Shipment</button>
                </div>
              </div>

              <h4>Open Purchase Orders</h4>
              <Table
                columns={[
                  { key: "po_id", label: "PO #", render: (r) => <span className="mono bold">{r.po_id}</span> },
                  { key: "created_at", label: "Date", render: (r) => <span className="small">{When(r.created_at)}</span> },
                  { key: "total_amount", label: "Value", render: (r) => <Money value={r.total_amount} /> },
                  { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                  {
                    key: "action",
                    label: "Action",
                    render: (r) => (
                      <div className="row">
                        {r.status === "SENT" && (
                          <button className="btn primary xs" onClick={() => ackPo(r.po_id)}>
                            Acknowledge PO
                          </button>
                        )}
                        <button className="btn ghost xs" onClick={() => toast(`Downloading PO ${r.po_id} PDF...`, "ok")}>
                          PDF
                        </button>
                      </div>
                    ),
                  },
                ]}
                rows={portalData.pos}
                empty="No purchase orders are currently assigned to your organization"
              />

              <div className="divider mt mb" />

              <h4>Inbound Advance Shipping Notices (ASN)</h4>
              <Table
                columns={[
                  { key: "asn_id", label: "ASN #", render: (r) => <span className="mono bold">{r.asn_id || "ASN-001"}</span> },
                  { key: "po_id", label: "Linked PO", render: (r) => <span className="mono">{r.po_id || "PO-00001"}</span> },
                  { key: "eta", label: "Expected Arrival", render: (r) => <span className="small">{When(r.eta)}</span> },
                  { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                ]}
                rows={portalData.asns}
                empty="No advance shipping notices submitted"
              />
              <div className="divider mt mb" />
              <h4>Submitted Supplier Invoices</h4>
              <Table
                rows={portalData.invoices}
                empty="No supplier invoices submitted"
                columns={[
                  { key: "invoice_id", label: "Invoice #", render: (r) => <span className="mono bold">{r.invoice_id}</span> },
                  { key: "supplier_invoice_number", label: "Supplier Reference" },
                  { key: "po_id", label: "PO #", render: (r) => <span className="mono">{r.po_id}</span> },
                  { key: "total_amount", label: "Amount", render: (r) => <Money value={r.total_amount} /> },
                  { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                ]}
              />
            </div>
          )}

          {activeTab === "Customer Portal View" && (
            <div className="card">
              <h3>B2B Hospital & Pharmacy Customer Portal</h3>
              <div className="card-sub">Order placement, shipment tracking, live POD documents, and invoice payment</div>
              <Table
                columns={[
                  { key: "order_id", label: "Sales Order", render: (r) => <span className="mono bold">{r.order_id || "SO-0001"}</span> },
                  { key: "created_at", label: "Date", render: (r) => <span className="small">{When(r.created_at)}</span> },
                  { key: "total_amount", label: "Amount", render: (r) => <Money value={r.total_amount} /> },
                  { key: "status", label: "Order Status", render: (r) => <Badge>{r.status}</Badge> },
                  { key: "shipments", label: "Shipment", render: (r) => <span className="mono small">{r.shipments?.at(-1) || "Not dispatched"}</span> },
                ]}
                rows={portalData.orders}
                empty="No sales orders are currently assigned to your organization"
              />
              <div className="divider mt mb" />
              <h4>Invoices</h4>
              <Table
                rows={portalData.invoices}
                empty="No invoices are currently assigned to your organization"
                columns={[
                  { key: "invoice_id", label: "Invoice #", render: (r) => <span className="mono bold">{r.invoice_id}</span> },
                  { key: "sales_order_id", label: "Sales Order", render: (r) => <span className="mono">{r.sales_order_id}</span> },
                  { key: "total_amount", label: "Amount", render: (r) => <Money value={r.total_amount} /> },
                  { key: "balance_amount", label: "Balance", render: (r) => <Money value={r.balance_amount ?? r.total_amount} /> },
                  { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                ]}
              />
              <div className="divider mt mb" />
              <h4>Shipments</h4>
              <Table
                rows={portalData.shipments}
                empty="No shipments are currently assigned to your organization"
                columns={[
                  { key: "shipment_id", label: "Shipment #", render: (r) => <span className="mono bold">{r.shipment_id}</span> },
                  { key: "sales_order_id", label: "Sales Order", render: (r) => <span className="mono">{r.sales_order_id}</span> },
                  { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                  { key: "updated_at", label: "Last Updated", render: (r) => <span className="small">{When(r.updated_at || r.created_at)}</span> },
                ]}
              />
            </div>
          )}

          {!activeTab && <div className="card">This account is not assigned to an external portal.</div>}
        </div>
      )}
      <Modal
        title="Create Advance Shipping Notice"
        sub="Confirm the purchase order, carrier, vehicle, and planned arrival. Line quantities are taken from the selected PO."
        open={showAsn}
        onClose={() => setShowAsn(false)}
        footer={<><button className="btn ghost" onClick={() => setShowAsn(false)}>Cancel</button><div className="spacer" /><button className="btn primary" onClick={createAsn}>Submit ASN</button></>}
      >
        <Field label="Purchase Order"><select value={asnForm.po_id} onChange={(e) => setAsnForm({ ...asnForm, po_id: e.target.value })}><option value="">Select PO</option>{portalData.pos.filter((po) => ["SENT", "ACKNOWLEDGED", "PARTIALLY_RECEIVED"].includes(po.status)).map((po) => <option key={po.po_id} value={po.po_id}>{po.po_id}</option>)}</select></Field>
        <Field label="Carrier"><input value={asnForm.carrier} onChange={(e) => setAsnForm({ ...asnForm, carrier: e.target.value })} /></Field>
        <Field label="Vehicle number"><input value={asnForm.vehicle_no} onChange={(e) => setAsnForm({ ...asnForm, vehicle_no: e.target.value })} /></Field>
        <Field label="Driver"><input value={asnForm.driver} onChange={(e) => setAsnForm({ ...asnForm, driver: e.target.value })} /></Field>
        <Field label="Planned arrival"><input type="datetime-local" value={asnForm.eta} onChange={(e) => setAsnForm({ ...asnForm, eta: e.target.value })} /></Field>
      </Modal>
      <Modal
        title="Submit Supplier Invoice"
        sub="The invoice is registered against the selected PO and enters the matching workflow."
        open={showInvoice}
        onClose={() => setShowInvoice(false)}
        footer={<><button className="btn ghost" onClick={() => setShowInvoice(false)}>Cancel</button><div className="spacer" /><button className="btn primary" onClick={submitInvoice}>Submit Invoice</button></>}
      >
        <Field label="Purchase Order"><select value={invoiceForm.po_id} onChange={(e) => setInvoiceForm({ ...invoiceForm, po_id: e.target.value })}><option value="">Select PO</option>{portalData.pos.map((po) => <option key={po.po_id} value={po.po_id}>{po.po_id}</option>)}</select></Field>
        <Field label="Supplier Invoice Number"><input value={invoiceForm.supplier_invoice_number} onChange={(e) => setInvoiceForm({ ...invoiceForm, supplier_invoice_number: e.target.value })} /></Field>
      </Modal>
    </div>
  );
}
