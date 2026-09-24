import React, { useEffect, useState } from "react";
import { api } from "../api";
import { Topbar } from "../App";
import { Badge, Table, When, useToast, CountTabs, Kpi, Skeleton, Money } from "../ui";

const TABS = ["Supplier Portal View", "Customer Portal View", "Inspector Workspace"];

export default function PortalsWorkspace() {
  const [tab, setTab] = useState("Supplier Portal View");
  const [portalData, setPortalData] = useState({ pos: [], rfqs: [], asns: [], invoices: [] });
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const { toast, toastHost } = useToast();

  const load = async () => {
    try {
      const [pos, rfqs, asns, invs] = await Promise.all([
        api("/api/portal/pos").catch(() => []),
        api("/api/portal/rfqs").catch(() => []),
        api("/api/logistics/inbound/asns").catch(() => []),
        api("/api/finance/supplier-invoices").catch(() => []),
      ]);
      setPortalData({
        pos: Array.isArray(pos) ? pos : [],
        rfqs: Array.isArray(rfqs) ? rfqs : [],
        asns: Array.isArray(asns) ? asns : [],
        invoices: Array.isArray(invs) ? invs : [],
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
      await api(`/api/portal/pos/${poId}/ack`, { method: "POST", body: {} });
      toast(`Purchase Order ${poId} acknowledged`, "ok");
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
        <Kpi ico="🤝" label="External Collaborators" value="18 Active" tone="blue" foot="Vendors, Buyers & Auditors" />
        <Kpi ico="🛒" label="Pending PO Acknowledgements" value={portalData.pos.filter((p) => p.status === "SENT").length} tone="orange" foot="Requires supplier ack" />
        <Kpi ico="🚚" label="Active ASNs & Shipments" value={portalData.asns.length} tone="blue" foot="In transit to dock" />
        <Kpi ico="⚖️" label="External Audit Sessions" value="1 Ready" tone="green" foot="Controlled read-only" />
      </div>

      <div className="row mb wrap">
        <CountTabs
          tabs={TABS.map((t) => ({ label: t }))}
          active={tab}
          onChange={setTab}
        />
        <div className="spacer" />
      </div>

      {loading ? (
        <Skeleton rows={6} />
      ) : (
        <div className="tab-panel">
          {tab === "Supplier Portal View" && (
            <div className="card">
              <div className="row mb">
                <div>
                  <h3>Supplier Self-Service Portal (Acme Corp View)</h3>
                  <div className="card-sub">Vendor portal view for receiving purchase orders, submitting ASNs, and uploading invoices</div>
                </div>
                <div className="spacer" />
                <button className="btn primary sm" onClick={() => toast("Opening ASN creation form...", "info")}>
                  + Create ASN Shipment
                </button>
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
                rows={portalData.pos.length > 0 ? portalData.pos : [
                  { po_id: "PO-00001", total_amount: 145000, status: "SENT", created_at: "2026-09-22" },
                  { po_id: "PO-00002", total_amount: 88000, status: "ACKNOWLEDGED", created_at: "2026-09-20" },
                ]}
              />

              <div className="divider mt mb" />

              <h4>Inbound Advance Shipping Notices (ASN)</h4>
              <Table
                columns={[
                  { key: "asn_id", label: "ASN #", render: (r) => <span className="mono bold">{r.asn_id || "ASN-001"}</span> },
                  { key: "po_id", label: "Linked PO", render: (r) => <span className="mono">{r.po_id || "PO-00001"}</span> },
                  { key: "expected_delivery", label: "Expected Arrival", render: (r) => <span className="small">{r.expected_delivery || "2026-09-25"}</span> },
                  { key: "status", label: "Status", render: (r) => <Badge>{r.status || "DISPATCHED"}</Badge> },
                ]}
                rows={portalData.asns.length > 0 ? portalData.asns : [
                  { asn_id: "ASN-00001", po_id: "PO-00001", expected_delivery: "2026-09-26", status: "IN_TRANSIT" },
                ]}
              />
            </div>
          )}

          {tab === "Customer Portal View" && (
            <div className="card">
              <h3>B2B Hospital & Pharmacy Customer Portal</h3>
              <div className="card-sub">Order placement, shipment tracking, live POD documents, and invoice payment</div>
              <Table
                columns={[
                  { key: "order_id", label: "Sales Order", render: (r) => <span className="mono bold">{r.order_id || "SO-0001"}</span> },
                  { key: "date", label: "Date", render: (r) => <span className="small">{r.date || "2026-09-24"}</span> },
                  { key: "amount", label: "Amount", render: (r) => <Money value={r.amount || 28500} /> },
                  { key: "status", label: "Order Status", render: (r) => <Badge>{r.status || "CONFIRMED"}</Badge> },
                  { key: "tracking", label: "Tracking #", render: (r) => <span className="mono small">{r.tracking || "TRK-981240"}</span> },
                ]}
                rows={[
                  { order_id: "SO-00001", date: "2026-09-24", amount: 48000, status: "DISPATCHED", tracking: "BLUEDART-84291" },
                  { order_id: "SO-00002", date: "2026-09-23", amount: 112000, status: "ALLOCATED", tracking: "SCHEDULED" },
                ]}
              />
            </div>
          )}

          {tab === "Inspector Workspace" && (
            <div className="card">
              <h3>Temporary Regulatory Inspector Workspace</h3>
              <div className="card-sub">Time-limited, tokenized read-only session with pre-scoped batch dossiers and audit trails</div>
              <div className="callout-box" style={{ background: "#f8fafc", padding: 16, borderRadius: 12, border: "1px solid var(--line)" }}>
                <b style={{ color: "var(--navy)", fontSize: 14, display: "block", marginBottom: 6 }}>🔒 Inspector Session Active: State FDA Inspection Session #981</b>
                <p className="small muted mb">
                  Authorized inspector has access to validated specifications, batch release dossiers, cleanroom HVAC differential pressure logs, and deviation investigation reports.
                </p>
                <div className="row wrap">
                  <button className="btn primary sm" onClick={() => toast("Exporting Inspection Evidence Index...", "ok")}>
                    📑 View Inspection Evidence Index
                  </button>
                  <button className="btn ghost sm" onClick={() => toast("Viewing Equipment Qualification Binders...", "ok")}>
                    ⚙️ View IQ/OQ/PQ Binders
                  </button>
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

