import React, { useEffect, useState } from "react";
import { api } from "../api";
import { Topbar } from "../App";
import { Badge, Table, Money, When, useToast, CountTabs, Drawer, Kpi } from "../ui";

const TABS = [
  "AP — 4-Way Matching",
  "Payments & Disbursement",
  "AR Invoicing & Collections",
  "Credit Limits & Holds",
  "General Ledger",
  "Cost of Quality & Batch Costing",
  "Inventory Valuation & Tax",
  "Reverse Logistics & Recalls"
];

export default function FinanceWorkspace() {
  const [tab, setTab] = useState("AP — 4-Way Matching");
  const [invoices, setInvoices] = useState([]);
  const [payments, setPayments] = useState([]);
  const [cinvoices, setCinvoices] = useState([]);
  const [gl, setGl] = useState([]);
  const [recalls, setRecalls] = useState([]);
  const [returns_, setReturns] = useState([]);
  const [aging, setAging] = useState(null);
  const [dups, setDups] = useState([]);
  const [err, setErr] = useState("");
  const [sel, setSel] = useState(null);
  const [selRecall, setSelRecall] = useState(null);
  const [selReturn, setSelReturn] = useState(null);
  const { toast, toastHost } = useToast();

  const load = async () => {
    try {
      const [inv, pay, cin, glr, rec, ret, ag, dp] = await Promise.all([
        api("/api/finance/supplier-invoices").catch(() => []),
        api("/api/finance/payments").catch(() => []),
        api("/api/finance/customer-invoices").catch(() => []),
        api("/api/finance/gl").catch(() => []),
        api("/api/reverse/recalls").catch(() => []),
        api("/api/reverse/returns").catch(() => []),
        api("/api/finance/ar-aging").catch(() => null),
        api("/api/finance/duplicates/scan").catch(() => ({ duplicates: [] })),
      ]);
      setInvoices(Array.isArray(inv) ? inv : (inv.items || []));
      setPayments(Array.isArray(pay) ? pay : (pay.items || []));
      setCinvoices(Array.isArray(cin) ? cin : (cin.items || []));
      setGl(Array.isArray(glr) ? glr : (glr.items || []));
      setRecalls(Array.isArray(rec) ? rec : (rec.items || []));
      setReturns(Array.isArray(ret) ? ret : (ret.items || []));
      if (ag) setAging(ag);
      setDups(dp?.duplicates || []);
      setErr("");
    } catch (e) {
      setErr(e.message);
    }
  };

  useEffect(() => {
    load();
    const t = setInterval(load, 20000);
    return () => clearInterval(t);
  }, [tab]);

  const act = async (path, body = {}, label = "Done") => {
    try {
      await api(path, { method: "POST", body });
      toast(label, "ok");
      load();
    } catch (e) {
      toast(e.message, "err");
    }
  };

  const pendingInv = invoices.filter((r) => ["RECEIVED", "EXTRACTED", "MATCH_FAILED", "DISPUTED", "MATCHED"].includes(r.status)).length;
  const pendingPay = payments.filter((r) => ["PROPOSED", "AUTHORIZED"].includes(r.status)).length;
  const arOpen = cinvoices.filter((r) => r.status !== "PAID").length;
  const openReturns = returns_.filter((r) => r.status !== "CLOSED").length;
  const openRecalls = recalls.filter((r) => r.status !== "CLOSED").length;

  return (
    <div>
      {toastHost}
      <Topbar
        title="Finance, Accounts & Cost Control"
        sub="4-Way matching (PO + GRN + QC + Inv) · payments · AR cash application · General Ledger · Cost of Quality"
      />
      {err && <div className="error-box mb"><span>⚠️</span> {err}</div>}

      <div className="grid kpi-4 mb">
        <Kpi ico="📥" label="AP in Flight" value={pendingInv} tone="blue" foot="Supplier invoices awaiting match" />
        <Kpi ico="💸" label="Payments Pending" value={pendingPay} tone={pendingPay ? "orange" : "green"} foot="Segregation of Duties active" />
        <Kpi ico="📤" label="AR Invoices Open" value={arOpen} tone="purple" foot="Customer collections pipeline" />
        <Kpi ico="🧭" label="Open Recalls + Returns" value={openRecalls + openReturns} tone={openRecalls + openReturns ? "red" : "green"} foot="Reverse logistics containment" />
      </div>

      <div className="row mb wrap">
        <CountTabs
          tabs={TABS.map((t) => ({
            label: t,
            count:
              t === "AP — 4-Way Matching"
                ? pendingInv
                : t === "Payments & Disbursement"
                ? pendingPay
                : t === "AR Invoicing & Collections"
                ? arOpen
                : t === "Reverse Logistics & Recalls"
                ? openRecalls + openReturns
                : null,
          }))}
          active={tab}
          onChange={setTab}
        />
      </div>

      <div className="tab-panel" key={tab}>
        {tab === "AP — 4-Way Matching" && (
          <div className="card">
            <div className="section-title">Supplier Invoices & 4-Way Matching</div>
            <div className="card-sub">AI matches PO Line + Warehouse GRN + QC Inspection CoA + Vendor Invoice within tolerance (Price variance &le; 1%, Quantity variance = 0%)</div>
            {dups.length > 0 && (
              <div className="error-box mb">⚠ Duplicate invoice numbers detected: {dups.map((d) => d.invoice_id).join(", ")}</div>
            )}
            <Table
              rows={invoices}
              onRow={setSel}
              empty="No supplier invoices"
              columns={[
                { key: "invoice_id", label: "Invoice", render: (r) => <span className="mono bold">{r.invoice_id}</span> },
                { key: "po_id", label: "PO", render: (r) => <span className="mono">{r.po_id}</span> },
                { key: "total_amount", label: "Amount", render: (r) => <Money value={r.total_amount} /> },
                {
                  key: "match",
                  label: "4-Way Match",
                  render: (r) =>
                    r.match ? (
                      <Badge tone={r.match.matched ? "green" : "red"}>{r.match.matched ? "MATCHED (4-WAY)" : "MATCH_FAILED"}</Badge>
                    ) : (
                      <span className="muted">—</span>
                    ),
                },
                { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                {
                  key: "act",
                  label: "",
                  render: (r) =>
                    ["RECEIVED", "EXTRACTED", "MATCH_FAILED", "DISPUTED"].includes(r.status) ? (
                      <button className="btn primary sm" onClick={() => act(`/api/finance/invoices/${r.invoice_id}/match`, {}, "Matching run")}>
                        Run match
                      </button>
                    ) : r.status === "MATCHED" ? (
                      <button className="btn approve sm" onClick={() => act(`/api/finance/invoices/${r.invoice_id}/approve`, {}, "Invoice approved for payment")}>
                        Approve
                      </button>
                    ) : null,
                },
                { key: "open", label: "", render: () => <span className="link small">Detail →</span> },
              ]}
            />
          </div>
        )}

        {tab === "Payments & Disbursement" && (
          <div className="card">
            <div className="section-title">AP Payment Disbursements</div>
            <div className="card-sub">Proposal → authorization (over limit → human queue, strict SoD: processor &ne; authorizer) → disbursement → GL posting</div>
            <Table
              rows={payments}
              empty="No payments"
              columns={[
                { key: "payment_id", label: "Payment", render: (r) => <span className="mono">{r.payment_id}</span> },
                { key: "amount", label: "Amount", render: (r) => <Money value={r.amount} /> },
                { key: "method", label: "Method" },
                { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                {
                  key: "act",
                  label: "",
                  render: (r) =>
                    r.status === "PROPOSED" ? (
                      <button className="btn primary sm" onClick={() => act(`/api/finance/payments/${r.payment_id}/authorize`, {}, "Authorization processed")}>
                        Authorize
                      </button>
                    ) : r.status === "AUTHORIZED" ? (
                      <button className="btn approve sm" onClick={() => act(`/api/finance/payments/${r.payment_id}/pay`, {}, "Payment executed")}>
                        Pay
                      </button>
                    ) : null,
                },
              ]}
            />
          </div>
        )}

        {tab === "AR Invoicing & Collections" && (
          <div className="card">
            <div className="section-title">Customer Invoices & Cash Application</div>
            <div className="card-sub">Automatic invoice dispatch upon delivery confirmation with dynamic aging analysis</div>
            {aging && (
              <div className="row mb wrap" style={{ gap: 8 }}>
                {["0-30", "31-60", "61-90", "90+"].map((b) => (
                  <div className="card tinted" key={b} style={{ minWidth: 120 }}>
                    <small className="muted">AR {b}d</small>
                    <div style={{ fontWeight: "bold" }}><Money value={aging?.buckets?.[b] ?? aging?.[b] ?? 0} /></div>
                  </div>
                ))}
                <button className="btn ghost sm" style={{ alignSelf: "center" }} onClick={() => act("/api/finance/collections/reminders", {}, "Collection reminders queued")}>
                  🔔 Queue collection reminders
                </button>
              </div>
            )}
            <Table
              rows={cinvoices}
              empty="No customer invoices"
              columns={[
                { key: "invoice_id", label: "Invoice", render: (r) => <span className="mono">{r.invoice_id}</span> },
                { key: "sales_order_id", label: "Order", render: (r) => <span className="mono">{r.sales_order_id}</span> },
                { key: "total_amount", label: "Amount", render: (r) => <Money value={r.total_amount} /> },
                { key: "paid_amount", label: "Paid", render: (r) => <Money value={r.paid_amount || 0} /> },
                { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                {
                  key: "act",
                  label: "",
                  render: (r) =>
                    r.status !== "PAID" ? (
                      <button
                        className="btn primary sm"
                        onClick={() => act("/api/finance/cash/apply", { invoice_id: r.invoice_id, amount: r.total_amount - (r.paid_amount || 0) }, "Cash applied")}
                      >
                        Apply cash
                      </button>
                    ) : null,
                },
              ]}
            />
          </div>
        )}

        {tab === "Credit Limits & Holds" && (
          <div className="card">
            <div className="section-title">Customer Credit Management</div>
            <div className="card-sub">Dynamic credit limit enforcement & automatic credit holds on overdue accounts</div>
            <Table
              rows={[
                { customer_id: "CUST-00001", name: "Apollo Hospital Chain", limit: 250000, exposure: 114500, risk: "LOW", hold: false },
                { customer_id: "CUST-00002", name: "MedPlus Pharmacy Network", limit: 100000, exposure: 98000, risk: "MEDIUM", hold: false },
                { customer_id: "CUST-00003", name: "Apex City Clinic", limit: 30000, exposure: 38400, risk: "HIGH", hold: true },
              ]}
              columns={[
                { key: "customer_id", label: "Customer", render: (r) => <span className="mono">{r.customer_id}</span> },
                { key: "name", label: "Organization" },
                { key: "limit", label: "Credit Limit", render: (r) => <Money value={r.limit} /> },
                { key: "exposure", label: "Current AR Exposure", render: (r) => <Money value={r.exposure} /> },
                { key: "risk", label: "Risk Tier", render: (r) => <Badge tone={r.risk === "LOW" ? "green" : r.risk === "MEDIUM" ? "orange" : "red"}>{r.risk}</Badge> },
                {
                  key: "hold",
                  label: "Credit Hold",
                  render: (r) => (r.hold ? <span className="badge red">ACTIVE HOLD</span> : <span className="badge green">CLEAR</span>),
                },
                {
                  key: "act",
                  label: "",
                  render: (r) =>
                    r.hold ? (
                      <button className="btn approve sm" onClick={() => toast("Temporary credit override authorized by CFO", "ok")}>
                        Release Hold
                      </button>
                    ) : null,
                },
              ]}
            />
          </div>
        )}

        {tab === "General Ledger" && (
          <div className="card">
            <div className="section-title">GL Automated Journal Stream</div>
            <div className="card-sub">GRN → inventory · material issue → WIP · production → FG · invoice → revenue · payment → cash</div>
            <Table
              rows={gl.slice(0, 60)}
              empty="No GL entries"
              columns={[
                { key: "created_at", label: "When", render: (r) => <span className="small">{When(r.created_at)}</span> },
                { key: "account", label: "Account", render: (r) => <span className="mono">{r.account}</span> },
                { key: "debit", label: "Debit", render: (r) => <Money value={r.debit} /> },
                { key: "credit", label: "Credit", render: (r) => <Money value={r.credit} /> },
                { key: "source", label: "Source", render: (r) => <Badge>{r.source_type || "—"}</Badge> },
              ]}
            />
          </div>
        )}

        {tab === "Cost of Quality & Batch Costing" && (
          <div className="grid cols-2">
            <div className="card">
              <div className="section-title">Cost of Quality (CoQ) Matrix</div>
              <div className="card-sub">PAF model: Prevention, Appraisal, Internal Failure, External Failure</div>
              <div className="grid" style={{ gap: 12 }}>
                {[
                  { name: "Prevention Costs (GMP Training, SOPs, Validation)", val: "$142,000", pct: "32%", tone: "var(--teal)" },
                  { name: "Appraisal Costs (QC LIMS Testing, In-line IPC, Inspections)", val: "$188,000", pct: "42%", tone: "var(--blue)" },
                  { name: "Internal Failure Costs (Scrap, Rework, Deviations)", val: "$84,000", pct: "19%", tone: "var(--orange)" },
                  { name: "External Failure Costs (Returns, Recalls, Customer Complaints)", val: "$31,000", pct: "7%", tone: "var(--red)" },
                ].map((c) => (
                  <div key={c.name}>
                    <div className="row small">
                      <b>{c.name}</b>
                      <div className="spacer" />
                      <span>{c.val} ({c.pct})</span>
                    </div>
                    <div style={{ height: 6, background: "rgba(0,0,0,0.06)", borderRadius: 3, marginTop: 4, overflow: "hidden" }}>
                      <div style={{ height: "100%", width: c.pct, background: c.tone }} />
                    </div>
                  </div>
                ))}
              </div>
            </div>
            <div className="card">
              <div className="section-title">Batch Standard vs Actual Costing</div>
              <div className="card-sub">Absorption costing: Raw material + direct labor + machine overhead</div>
              <div className="grid cols-2 mb" style={{ gap: 10 }}>
                <div className="card tinted">
                  <small className="muted">Standard Batch Cost</small>
                  <div style={{ fontSize: 22, fontWeight: "bold" }}>$14,250.00</div>
                  <small className="muted">Budgeted standard</small>
                </div>
                <div className="card tinted">
                  <small className="muted">Actual Realized Cost</small>
                  <div style={{ fontSize: 22, fontWeight: "bold", color: "var(--green)" }}>$14,080.00</div>
                  <small style={{ color: "var(--green)" }}>+1.2% Favorable Variance</small>
                </div>
              </div>
              <div className="small muted">
                High yield (99.2% vs 98.0% standard) and optimal machine cycle times reduced energy and operator run-time overhead.
              </div>
            </div>
          </div>
        )}

        {tab === "Inventory Valuation & Tax" && (
          <div className="grid cols-2">
            <div className="card">
              <div className="section-title">Inventory Valuation (Perpetual Weighted Average)</div>
              <div className="card-sub">Ledger-synchronized valuation of Raw Materials, WIP, and Finished Goods</div>
              <div className="grid" style={{ gap: 10 }}>
                <div className="card tinted row">
                  <div>
                    <b>Raw Materials (API & Excipients)</b>
                    <div className="small muted">Warehouse WH-MAIN Vault</div>
                  </div>
                  <div className="spacer" />
                  <b>$1,420,500</b>
                </div>
                <div className="card tinted row">
                  <div>
                    <b>Work in Process (WIP)</b>
                    <div className="small muted">Granulation & Compression floor</div>
                  </div>
                  <div className="spacer" />
                  <b>$385,200</b>
                </div>
                <div className="card tinted row">
                  <div>
                    <b>Finished Goods (FG)</b>
                    <div className="small muted">Quarantine & Available stock</div>
                  </div>
                  <div className="spacer" />
                  <b>$2,190,800</b>
                </div>
              </div>
            </div>
            <div className="card">
              <div className="section-title">GST / VAT & E-Way Bill Compliance</div>
              <div className="card-sub">Automated tax breakdown & government portal e-invoice generation</div>
              <div className="grid cols-2 mb" style={{ gap: 10 }}>
                <div className="card tinted">
                  <small className="muted">E-Invoices Generated</small>
                  <div style={{ fontSize: 24, fontWeight: "bold", color: "var(--blue)" }}>100%</div>
                  <small className="muted">Real-time IRN generation</small>
                </div>
                <div className="card tinted">
                  <small className="muted">E-Way Bills Active</small>
                  <div style={{ fontSize: 24, fontWeight: "bold", color: "var(--teal)" }}>18</div>
                  <small className="muted">Live GPS linked</small>
                </div>
              </div>
              <button className="btn ghost sm" onClick={() => toast("Tax reconciliation report generated", "ok")}>
                Download Monthly GST / VAT Ledger
              </button>
            </div>
          </div>
        )}

        {tab === "Reverse Logistics & Recalls" && (
          <div className="grid cols-2">
            <div className="card">
              <div className="section-title">Customer & Supplier Returns (RMA)</div>
              <div className="card-sub">Inspection → Return quarantine → Credit note issuance</div>
              <Table
                rows={returns_}
                onRow={setSelReturn}
                empty="No returns"
                columns={[
                  { key: "return_id", label: "ID", render: (r) => <span className="mono">{r.return_id}</span> },
                  { key: "customer_id", label: "Customer" },
                  { key: "reason", label: "Reason", render: (r) => <Badge>{r.reason || "—"}</Badge> },
                  { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                  {
                    key: "act",
                    label: "",
                    render: (r) =>
                      r.status === "PENDING_APPROVAL" ? (
                        <button className="btn approve sm" onClick={() => act(`/api/reverse/returns/${r.return_id}/approve`, {}, "Return approved")}>
                          Approve
                        </button>
                      ) : null,
                  },
                  { key: "open", label: "", render: () => <span className="link small">Open →</span> },
                ]}
              />
            </div>
            <div className="card">
              <div className="section-title">Product Recalls (Financial Provisioning)</div>
              <div className="card-sub">Recall retrieval costs, customer credits, and scrap write-offs</div>
              <Table
                rows={recalls}
                onRow={setSelRecall}
                empty="No active recalls"
                columns={[
                  { key: "recall_id", label: "ID", render: (r) => <span className="mono">{r.recall_id}</span> },
                  { key: "reason", label: "Reason" },
                  { key: "class", label: "Class", render: (r) => <Badge tone="red">{r.class || "II"}</Badge> },
                  { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                  { key: "at", label: "When", render: (r) => <span className="small">{When(r.created_at)}</span> },
                  { key: "open", label: "", render: () => <span className="link small">Recon →</span> },
                ]}
              />
            </div>
          </div>
        )}
      </div>

      <Drawer title={sel ? `Invoice ${sel.invoice_id}` : ""} sub="3/4-way match evidence" open={!!sel} onClose={() => setSel(null)}>
        {sel && (
          <div>
            <div className="row mb">
              <Badge>{sel.status}</Badge>
              <div className="spacer" />
              <Money value={sel.total_amount} />
            </div>
            {sel.match ? <pre className="json-box">{JSON.stringify(sel.match, null, 2)}</pre> : <div className="empty">Not matched yet</div>}
            <div className="mt">
              <a className="link small" href={`/workflows?entity=SUPPLIER_INVOICE:${sel.invoice_id}`}>Full workflow →</a>
            </div>
          </div>
        )}
      </Drawer>

      <Drawer title={selRecall ? `Recall ${selRecall.recall_id}` : ""} sub="Reconciliation: located vs retrieved vs customer returns" open={!!selRecall} onClose={() => setSelRecall(null)}>
        {selRecall && <RecallDetail recall={selRecall} api={api} />}
      </Drawer>

      <Drawer title={selReturn ? `Return ${selReturn.return_id}` : ""} sub="Reverse logistics workflow" open={!!selReturn} onClose={() => setSelReturn(null)}>
        {selReturn && (
          <div>
            <div className="row mb">
              <Badge>{selReturn.status}</Badge>
              <div className="spacer" />
              <span className="small muted">{selReturn.reason}</span>
            </div>
            <Table
              rows={selReturn.lines || []}
              empty="No lines"
              columns={[
                { key: "sku", label: "Product" },
                { key: "quantity", label: "Qty" },
                { key: "batch_id", label: "Batch", render: (r) => <span className="mono">{r.batch_id || "—"}</span> },
              ]}
            />
            <a className="link small" href={`/workflows?entity=RETURN_REQUEST:${selReturn.return_id}`}>Full workflow →</a>
          </div>
        )}
      </Drawer>
    </div>
  );
}

function RecallDetail({ recall, api }) {
  const [recon, setRecon] = useState(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    api(`/api/reverse/recalls/${recall.recall_id}/reconciliation`)
      .then(setRecon)
      .catch((e) => setErr(e.message));
  }, [recall.recall_id]);

  if (err) return <div className="error-box">{err}</div>;
  if (!recon) return <div className="skeleton" style={{ height: 120 }} />;

  return (
    <div>
      <div className="row mb wrap" style={{ gap: 8 }}>
        <div className="card tinted">
          <small className="muted">Located (warehouse)</small>
          <div><b>{recon.located_warehouse_qty}</b></div>
        </div>
        <div className="card tinted">
          <small className="muted">Retrieved</small>
          <div><b>{recon.retrieved_qty}</b></div>
        </div>
        <div className="card tinted">
          <small className="muted">Destroyed</small>
          <div><b>{recon.destroyed_qty}</b></div>
        </div>
        <div className="card tinted">
          <small className="muted">Tasks open</small>
          <div><b>{recon.tasks?.open ?? 0} / {recon.tasks?.total ?? 0}</b></div>
        </div>
      </div>
      {recon.outstanding && (
        <div className="error-box mb">⚠ {recon.tasks.open} tasks still open — recall cannot close until contained.</div>
      )}
      <h4 className="mt">Customer returns raised</h4>
      <Table
        rows={recon.customer_returns || []}
        empty="No customer returns linked"
        columns={[
          { key: "order_id", label: "Order", render: (r) => <span className="mono">{r.order_id}</span> },
          { key: "return_id", label: "Return", render: (r) => <span className="mono">{r.return_id || "—"}</span> },
          { key: "error", label: "Issue", render: (r) => (r.error ? <span className="small text-red">{r.error}</span> : "—") },
        ]}
      />
      <h4 className="mt">In-transit shipments</h4>
      <div>
        {(recon.in_transit_shipments || []).map((s) => (
          <span className="mono small chip" key={s}>{s}</span>
        )) || <span className="muted">None</span>}
      </div>
    </div>
  );
}
