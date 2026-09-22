import React, { useEffect, useState } from "react";
import { api } from "../api";
import { Topbar } from "../App";
import { Badge, Table, Money, When, useToast } from "../ui";

const TABS = ["AP — Supplier Invoices", "Payments", "AR — Customer Invoices", "General Ledger", "Governance"];

export default function FinanceWorkspace() {
  const [tab, setTab] = useState("AP — Supplier Invoices");
  const [invoices, setInvoices] = useState([]);
  const [payments, setPayments] = useState([]);
  const [cinvoices, setCinvoices] = useState([]);
  const [gl, setGl] = useState([]);
  const [recalls, setRecalls] = useState([]);
  const [returns_, setReturns] = useState([]);
  const [err, setErr] = useState("");
  const [sel, setSel] = useState(null);
  const toast = useToast();

  const load = async () => {
    try {
      const [inv, pay, cin, glr, rec, ret] = await Promise.all([
        api("/api/finance/supplier-invoices"), api("/api/finance/payments").catch(() => []),
        api("/api/finance/customer-invoices").catch(() => []),
        api("/api/finance/gl").catch(() => []),
        api("/api/reverse/recalls").catch(() => []),
        api("/api/reverse/returns").catch(() => []),
      ]);
      setInvoices(Array.isArray(inv) ? inv : inv.items || []);
      setPayments(Array.isArray(pay) ? pay : pay.items || []);
      setCinvoices(Array.isArray(cin) ? cin : cin.items || []);
      setGl(Array.isArray(glr) ? glr : glr.items || []);
      setRecalls(Array.isArray(rec) ? rec : rec.items || []);
      setReturns(Array.isArray(ret) ? ret : ret.items || []);
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
      <Topbar title="Finance & Governance"
              sub="2/3/4-way matching · AP · payments · AR · cash · GL events from every domain · recall & returns" />
      {err && <div className="error-box mb">{err}</div>}
      <div className="row mb" style={{ flexWrap: "wrap" }}>
        {TABS.map((t) => (
          <button key={t} className={`btn ${tab === t ? "primary" : "ghost"}`} onClick={() => setTab(t)}>{t}</button>
        ))}
        <div className="spacer" />
        <button className="btn ghost"
                onClick={() => act("/api/agents/run/finance-agent", { goal: "ap_run" }, "Finance agent run started")}>
          🤖 Run Finance Agent
        </button>
      </div>

      {tab === "AP — Supplier Invoices" && (
        <div className="card">
          <h3>Supplier invoices & matching</h3>
          <div className="card-sub">The Finance Agent investigates mismatches against QA records and requests supplier corrections before any human sees it</div>
          <Table rows={invoices} onRow={setSel} empty="No supplier invoices"
            columns={[
              { key: "invoice_id", label: "Invoice", render: (r) => <span className="mono">{r.invoice_id}</span> },
              { key: "po_id", label: "PO", render: (r) => <span className="mono">{r.po_id}</span> },
              { key: "total_amount", label: "Amount", render: (r) => <Money value={r.total_amount} /> },
              { key: "match", label: "Match", render: (r) => r.match
                  ? <Badge value={r.match.matched ? "MATCHED" : "MATCH_FAILED"} /> : "—"},
              { key: "status", label: "Status", render: (r) => <Badge value={r.status} /> },
              { key: "act", label: "", render: (r) => ["RECEIVED", "EXTRACTED", "MATCH_FAILED", "DISPUTED"].includes(r.status) ? (
                <button className="btn primary sm" onClick={() =>
                  act(`/api/finance/invoices/${r.invoice_id}/match`, {}, "Matching run")}>Run match</button>
              ) : r.status === "MATCHED" ? (
                <button className="btn approve sm" onClick={() =>
                  act(`/api/finance/invoices/${r.invoice_id}/approve`, {}, "Invoice approved for payment")}>Approve</button>
              ) : null },
            ]} />
        </div>
      )}

      {tab === "Payments" && (
        <div className="card">
          <h3>AP payments</h3>
          <div className="card-sub">Proposal → authorization (over limit → decision queue) → payment → GL posting</div>
          <Table rows={payments} empty="No payments"
            columns={[
              { key: "payment_id", label: "Payment", render: (r) => <span className="mono">{r.payment_id}</span> },
              { key: "amount", label: "Amount", render: (r) => <Money value={r.amount} /> },
              { key: "method", label: "Method" },
              { key: "status", label: "Status", render: (r) => <Badge value={r.status} /> },
              { key: "act", label: "", render: (r) => r.status === "PROPOSED" ? (
                <button className="btn primary sm" onClick={() =>
                  act(`/api/finance/payments/${r.payment_id}/authorize`, {}, "Authorization processed")}>Authorize</button>
              ) : r.status === "AUTHORIZED" ? (
                <button className="btn approve sm" onClick={() =>
                  act(`/api/finance/payments/${r.payment_id}/pay`, {}, "Payment executed")}>Pay</button>
              ) : null },
            ]} />
        </div>
      )}

      {tab === "AR — Customer Invoices" && (
        <div className="card">
          <h3>Customer invoices & cash application</h3>
          <Table rows={cinvoices} empty="No customer invoices"
            columns={[
              { key: "invoice_id", label: "Invoice", render: (r) => <span className="mono">{r.invoice_id}</span> },
              { key: "sales_order_id", label: "Order", render: (r) => <span className="mono">{r.sales_order_id}</span> },
              { key: "total_amount", label: "Amount", render: (r) => <Money value={r.total_amount} /> },
              { key: "paid_amount", label: "Paid", render: (r) => <Money value={r.paid_amount || 0} /> },
              { key: "status", label: "Status", render: (r) => <Badge value={r.status} /> },
              { key: "act", label: "", render: (r) => r.status !== "PAID" ? (
                <button className="btn primary sm" onClick={() =>
                  act("/api/finance/cash/apply", { invoice_id: r.invoice_id,
                      amount: r.total_amount - (r.paid_amount || 0) }, "Cash applied")}>Apply cash</button>
              ) : null },
            ]} />
        </div>
      )}

      {tab === "General Ledger" && (
        <div className="card">
          <h3>GL event stream</h3>
          <div className="card-sub">GRN → inventory · material issue → WIP · production → FG · invoice → revenue · payment → cash</div>
          <Table rows={gl.slice(0, 60)} empty="No GL entries"
            columns={[
              { key: "created_at", label: "When", render: (r) => When(r.created_at) },
              { key: "account", label: "Account", render: (r) => <span className="mono">{r.account}</span> },
              { key: "debit", label: "Debit", render: (r) => <Money value={r.debit} /> },
              { key: "credit", label: "Credit", render: (r) => <Money value={r.credit} /> },
              { key: "source", label: "Source", render: (r) => <Badge value={r.source_type || "—"} /> },
            ]} />
        </div>
      )}

      {tab === "Governance" && (
        <div className="grid cols-2">
          <div className="card">
            <h3>Recalls</h3>
            <div className="card-sub">Global batch block → quarantine tasks → reconciliation → QA closes</div>
            <Table rows={recalls} empty="No recalls"
              columns={[
                { key: "recall_id", label: "ID", render: (r) => <span className="mono">{r.recall_id}</span> },
                { key: "reason", label: "Reason" },
                { key: "class", label: "Class", render: (r) => <Badge value={r.class || "II"} /> },
                { key: "status", label: "Status", render: (r) => <Badge value={r.status} /> },
              ]} />
          </div>
          <div className="card">
            <h3>Returns (reverse logistics)</h3>
            <div className="card-sub">RMA → pickup → receive → RETURN_QUARANTINE → inspect → disposition</div>
            <Table rows={returns_} empty="No returns"
              columns={[
                { key: "return_id", label: "ID", render: (r) => <span className="mono">{r.return_id}</span> },
                { key: "customer_id", label: "Customer" },
                { key: "status", label: "Status", render: (r) => <Badge value={r.status} /> },
                { key: "created_at", label: "Raised", render: (r) => When(r.created_at) },
              ]} />
          </div>
        </div>
      )}

      {sel && (
        <div className="card mt">
          <div className="row">
            <h3>Invoice {sel.invoice_id} — match detail</h3>
            <div className="spacer" />
            <Badge value={sel.status} />
            <button className="btn ghost sm" onClick={() => setSel(null)}>Close</button>
          </div>
          {sel.match ? (
            <pre className="mono" style={{ background: "var(--blue-soft)", padding: 12, borderRadius: 10,
                  fontSize: 11.5, overflow: "auto", marginTop: 10 }}>
              {JSON.stringify(sel.match, null, 2)}
            </pre>
          ) : <div className="empty">Not matched yet</div>}
          <a className="small" style={{ color: "var(--blue)", fontWeight: 700 }}
             href={`/workflows?entity=SUPPLIER_INVOICE:${sel.invoice_id}`}>Full workflow →</a>
        </div>
      )}
    </div>
  );
}
