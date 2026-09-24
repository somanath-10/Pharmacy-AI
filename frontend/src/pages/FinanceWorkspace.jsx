import React, { useEffect, useState } from "react";
import { api } from "../api";
import { Topbar } from "../App";
import { Badge, Table, Money, When, useToast, CountTabs, Drawer, Kpi } from "../ui";

const TABS = ["AP — Supplier Invoices", "Payments", "AR — Customer Invoices", "General Ledger",
  "Returns", "Recall Center", "Pharmacovigilance", "AI Agents", "Governance"];

export default function FinanceWorkspace() {
  const [tab, setTab] = useState("AP — Supplier Invoices");
  const [invoices, setInvoices] = useState([]);
  const [payments, setPayments] = useState([]);
  const [cinvoices, setCinvoices] = useState([]);
  const [gl, setGl] = useState([]);
  const [recalls, setRecalls] = useState([]);
  const [returns_, setReturns] = useState([]);
  const [aging, setAging] = useState(null);
  const [dups, setDups] = useState([]);
  const [safetyCases, setSafetyCases] = useState([]);
  const [safetySignals, setSafetySignals] = useState([]);
  const [agentCalls, setAgentCalls] = useState([]);
  const [agents, setAgents] = useState([]);
  const [anomalies, setAnomalies] = useState(null);
  const [err, setErr] = useState("");
  const [sel, setSel] = useState(null);
  const [selRecall, setSelRecall] = useState(null);
  const [selReturn, setSelReturn] = useState(null);
  const { toast, toastHost } = useToast();

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
      if (tab === "Returns" || tab === "Recall Center" || tab === "Pharmacovigilance"
          || tab === "AI Agents") {
        const extras = await Promise.all([
          api("/api/finance/ar-aging").catch(() => null),
          api("/api/finance/duplicates/scan").catch(() => ({ duplicates: [] })),
          api("/api/safety/cases").catch(() => []),
          api("/api/safety/signals").catch(() => []),
          api("/api/agents/tool-calls").catch(() => []),
          api("/api/agents").catch(() => []),
          api("/api/finance/anomalies/compliance").catch(() => null),
        ]);
        if (extras[0]) setAging(extras[0]);
        setDups(extras[1]?.duplicates || []);
        setSafetyCases(Array.isArray(extras[2]) ? extras[2] : extras[2]?.items || []);
        setSafetySignals(Array.isArray(extras[3]) ? extras[3] : extras[3]?.items || []);
        setAgentCalls(Array.isArray(extras[4]) ? extras[4] : extras[4]?.items || []);
        setAgents(Array.isArray(extras[5]) ? extras[5] : extras[5]?.items || []);
        if (extras[6]) setAnomalies(extras[6]);
      }
      setErr("");
    } catch (e) { setErr(e.message); }
  };
  useEffect(() => { load(); const t = setInterval(load, 20000); return () => clearInterval(t); }, [tab]);

  const act = async (path, body = {}, label = "Done") => {
    try { await api(path, { method: "POST", body }); toast(label, "ok"); load(); }
    catch (e) { toast(e.message, "err"); }
  };

  const pendingInv = invoices.filter((r) => ["RECEIVED", "EXTRACTED", "MATCH_FAILED", "DISPUTED", "MATCHED"].includes(r.status)).length;
  const pendingPay = payments.filter((r) => ["PROPOSED", "AUTHORIZED"].includes(r.status)).length;
  const arOpen = cinvoices.filter((r) => r.status !== "PAID").length;
  const openReturns = returns_.filter((r) => r.status !== "CLOSED").length;
  const openRecalls = recalls.filter((r) => r.status !== "CLOSED").length;

  return (
    <div>
      {toastHost}
      <Topbar title="Finance & Governance"
              sub="2/3/4-way matching · AP · payments · AR · returns · recall · PV · AI agents" />
      {err && <div className="error-box mb">{err}</div>}

      <div className="grid kpi-4 mb">
        <Kpi ico="📥" label="AP in flight" value={pendingInv} tone="blue" />
        <Kpi ico="💸" label="Payments pending" value={pendingPay} tone={pendingPay ? "orange" : "green"} />
        <Kpi ico="📤" label="AR open" value={arOpen} tone="purple" />
        <Kpi ico="🧭" label="Open recalls + returns" value={openRecalls + openReturns}
             tone={openRecalls + openReturns ? "red" : "green"} />
      </div>

      <div className="row mb wrap">
        <CountTabs tabs={TABS.map((t) => ({
          label: t,
          count: t === "AP — Supplier Invoices" ? pendingInv : t === "Payments" ? pendingPay
            : t === "AR — Customer Invoices" ? arOpen : t === "Returns" ? openReturns
            : t === "Recall Center" ? openRecalls : t === "Pharmacovigilance" ? safetyCases.filter((c) => c.status !== "CLOSED").length
            : t === "AI Agents" ? agentCalls.filter((c) => c.status === "RUNNING" || c.status === "FAILED").length : null,
        }))} active={tab} onChange={setTab} />
      </div>

      <div className="tab-panel" key={tab}>
        {tab === "AP — Supplier Invoices" && (
          <div className="card">
            <h3>Supplier invoices & matching</h3>
            <div className="card-sub">The Finance Agent investigates mismatches against QA records and requests supplier corrections before any human sees it. Click a row for match detail.</div>
            {dups.length > 0 && (
              <div className="error-box mb">⚠ Duplicate invoice numbers detected: {dups.map((d) => d.invoice_id).join(", ")}</div>
            )}
            <Table rows={invoices} onRow={setSel} empty="No supplier invoices"
              columns={[
                { key: "invoice_id", label: "Invoice", render: (r) => <span className="mono">{r.invoice_id}</span> },
                { key: "po_id", label: "PO", render: (r) => <span className="mono">{r.po_id}</span> },
                { key: "total_amount", label: "Amount", render: (r) => <Money value={r.total_amount} /> },
                { key: "match", label: "Match", render: (r) => r.match
                    ? <Badge>{r.match.matched ? "MATCHED" : "MATCH_FAILED"}</Badge> : <span className="muted">—</span>},
                { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                { key: "act", label: "", render: (r) => ["RECEIVED", "EXTRACTED", "MATCH_FAILED", "DISPUTED"].includes(r.status) ? (
                  <button className="btn primary sm" onClick={() =>
                    act(`/api/finance/invoices/${r.invoice_id}/match`, {}, "Matching run")}>Run match</button>
                ) : r.status === "MATCHED" ? (
                  <button className="btn approve sm" onClick={() =>
                    act(`/api/finance/invoices/${r.invoice_id}/approve`, {}, "Invoice approved for payment")}>Approve</button>
                ) : null },
                { key: "open", label: "", render: () => <span className="link small">Open →</span> },
              ]} />
          </div>
        )}

        {tab === "Payments" && (
          <div className="card">
            <h3>AP payments</h3>
            <div className="card-sub">Proposal → authorization (over limit → decision queue, SoD: processor ≠ authorizer) → payment → GL posting</div>
            <Table rows={payments} empty="No payments"
              columns={[
                { key: "payment_id", label: "Payment", render: (r) => <span className="mono">{r.payment_id}</span> },
                { key: "amount", label: "Amount", render: (r) => <Money value={r.amount} /> },
                { key: "method", label: "Method" },
                { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
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
            {aging && (
              <div className="row mb wrap gap">
                {["0-30", "31-60", "61-90", "90+"].map((b) => (
                  <div className="card tinted" key={b}>
                    <small className="muted">AR {b}d</small>
                    <div><Money value={aging?.buckets?.[b] ?? aging?.[b] ?? 0} /></div>
                  </div>
                ))}
                <button className="btn ghost sm" onClick={() =>
                  act("/api/finance/collections/reminders", {}, "Collection reminders queued")}>
                  🔔 Queue collection reminders
                </button>
              </div>
            )}
            <Table rows={cinvoices} empty="No customer invoices"
              columns={[
                { key: "invoice_id", label: "Invoice", render: (r) => <span className="mono">{r.invoice_id}</span> },
                { key: "sales_order_id", label: "Order", render: (r) => <span className="mono">{r.sales_order_id}</span> },
                { key: "total_amount", label: "Amount", render: (r) => <Money value={r.total_amount} /> },
                { key: "paid_amount", label: "Paid", render: (r) => <Money value={r.paid_amount || 0} /> },
                { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
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
                { key: "created_at", label: "When", render: (r) => <span className="small">{When(r.created_at)}</span> },
                { key: "account", label: "Account", render: (r) => <span className="mono">{r.account}</span> },
                { key: "debit", label: "Debit", render: (r) => <Money value={r.debit} /> },
                { key: "credit", label: "Credit", render: (r) => <Money value={r.credit} /> },
                { key: "source", label: "Source", render: (r) => <Badge>{r.source_type || "—"}</Badge> },
              ]} />
          </div>
        )}

        {tab === "Returns" && (
          <div className="card">
            <h3>Customer & supplier returns</h3>
            <div className="card-sub">RMA → approval → pickup → receive → RETURN_QUARANTINE → inspect → restock / RTV / reject / dispose → credit note</div>
            <Table rows={returns_} onRow={setSelReturn} empty="No returns"
              columns={[
                { key: "return_id", label: "ID", render: (r) => <span className="mono">{r.return_id}</span> },
                { key: "customer_id", label: "Customer" },
                { key: "reason", label: "Reason", render: (r) => <Badge>{r.reason || "—"}</Badge> },
                { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                { key: "act", label: "", render: (r) => r.status === "PENDING_APPROVAL" ? (
                  <button className="btn approve sm" onClick={() =>
                    act(`/api/reverse/returns/${r.return_id}/approve`, {}, "Return approved")}>Approve</button>
                ) : null },
                { key: "open", label: "", render: () => <span className="link small">Open →</span> },
              ]} />
          </div>
        )}

        {tab === "Recall Center" && (
          <div className="grid cols-2">
            <div className="card" style={{ gridColumn: "1 / -1" }}>
              <h3>Recalls</h3>
              <div className="card-sub">Global batch block → warehouse / in-transit / customer tasks → reconciliation → QA authority closes. Click a row for reconciliation detail.</div>
              <Table rows={recalls} onRow={setSelRecall} empty="No recalls"
                columns={[
                  { key: "recall_id", label: "ID", render: (r) => <span className="mono">{r.recall_id}</span> },
                  { key: "reason", label: "Reason" },
                  { key: "class", label: "Class", render: (r) => <Badge>{r.class || "II"}</Badge> },
                  { key: "batches", label: "Batches", render: (r) => (r.batch_ids || []).length },
                  { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                ]} />
            </div>
          </div>
        )}

        {tab === "Pharmacovigilance" && (
          <div className="grid cols-2">
            <div className="card">
              <h3>Adverse-event cases</h3>
              <div className="card-sub">Intake → triage → duplicate check → medical review → follow-up → close. Serious cases go to the human decision queue.</div>
              <Table rows={safetyCases} empty="No PV cases"
                columns={[
                  { key: "case_id", label: "Case", render: (r) => <span className="mono">{r.case_id}</span> },
                  { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                  { key: "serious", label: "Serious", render: (r) => r.serious ? <Badge>SERIOUS</Badge> : <span className="muted">no</span> },
                  { key: "created_at", label: "Opened", render: (r) => <span className="small">{When(r.created_at)}</span> },
                ]} />
            </div>
            <div className="card">
              <h3>Signals & PSUR</h3>
              <div className="card-sub">Emerging safety signals from case clusters</div>
              <Table rows={safetySignals} empty="No signals tracked"
                columns={[
                  { key: "signal_id", label: "Signal", render: (r) => <span className="mono">{r.signal_id}</span> },
                  { key: "product_id", label: "Product" },
                  { key: "status", label: "Status", render: (r) => <Badge>{r.status || "OPEN"}</Badge> },
                ]} />
            </div>
          </div>
        )}

        {tab === "AI Agents" && (
          <div className="grid cols-2">
            <div className="card">
              <h3>Agent fleet</h3>
              <div className="card-sub">Every agent action flows the gateway: permission → policy → domain API → event → audit</div>
              <Table rows={agents} empty="No agents registered"
                columns={[
                  { key: "agent_id", label: "Agent", render: (r) => <b>{r.agent_id || r.id}</b> },
                  { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                  { key: "runs", label: "Runs" },
                  { key: "errors", label: "Errors", render: (r) => <span className={r.errors ? "text-red" : ""}>{r.errors}</span> },
                ]} />
            </div>
            <div className="card">
              <h3>Recent tool calls</h3>
              <div className="card-sub">Agent activity log — reason, confidence, model and escalation metadata recorded per call</div>
              <Table rows={agentCalls.slice(0, 30)} empty="No agent activity"
                columns={[
                  { key: "agent", label: "Agent", render: (r) => <span className="mono small">{r.agent}</span> },
                  { key: "tool", label: "Tool", render: (r) => <span className="mono small">{r.tool}</span> },
                  { key: "reason", label: "Reason", render: (r) => <span className="small muted">{r.reason || "—"}</span> },
                  { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                  { key: "at", label: "When", render: (r) => <span className="small">{When(r.created_at)}</span> },
                ]} />
            </div>
            <div className="card" style={{ gridColumn: "1 / -1" }}>
              <h3>Compliance anomaly sweep</h3>
              <div className="card-sub">Role changes · after-hours payments · repeated failed approvals · policy violations</div>
              <button className="btn primary sm mb" onClick={async () => {
                try { setAnomalies(await api("/api/finance/anomalies/compliance")); toast("Sweep complete", "ok"); }
                catch (e) { toast(e.message, "err"); }
              }}>🛡 Run sweep now</button>
              {anomalies && (
                <div className="row wrap gap">
                  <div className="card tinted" style={{ minWidth: 180 }}>
                    <small className="muted text-red">HIGH risk</small>
                    <div><b>{(anomalies.high || []).length}</b> findings</div>
                    {(anomalies.high || []).slice(0, 3).map((f, i) => (
                      <div className="small muted" key={i}>{f.kind} — {f.payment || f.entity || ""}</div>
                    ))}
                  </div>
                  <div className="card tinted" style={{ minWidth: 180 }}>
                    <small className="muted text-orange">MEDIUM risk</small>
                    <div><b>{(anomalies.medium || []).length}</b> findings</div>
                    {(anomalies.medium || []).slice(0, 3).map((f, i) => (
                      <div className="small muted" key={i}>{f.kind}</div>
                    ))}
                  </div>
                  <div className="card tinted" style={{ minWidth: 180 }}>
                    <small className="muted">Checks run</small>
                    {(anomalies.checked || []).map((c, i) => <div className="small muted" key={i}>{c}</div>)}
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        {tab === "Governance" && (
          <div className="grid cols-2">
            <div className="card">
              <h3>Decision queue</h3>
              <div className="card-sub">Human authority: high-value payments, recalls, serious AEs, fraud alerts</div>
              <a className="link" href="/queue">Open Human Decision Queue →</a>
            </div>
            <div className="card">
              <h3>Audit trail</h3>
              <div className="card-sub">Every security- and finance-relevant action</div>
              <a className="link" href="/workflows">Open Workflow Viewer →</a>
            </div>
          </div>
        )}
      </div>

      <Drawer title={sel ? `Invoice ${sel.invoice_id}` : ""} sub="3/4-way match evidence"
              open={!!sel} onClose={() => setSel(null)}>
        {sel && (
          <div>
            <div className="row mb">
              <Badge>{sel.status}</Badge>
              <div className="spacer" />
              <Money value={sel.total_amount} />
            </div>
            {sel.match
              ? <pre className="json-box">{JSON.stringify(sel.match, null, 2)}</pre>
              : <div className="empty">Not matched yet</div>}
            <div className="mt">
              <a className="link small" href={`/workflows?entity=SUPPLIER_INVOICE:${sel.invoice_id}`}>Full workflow →</a>
            </div>
          </div>
        )}
      </Drawer>

      <Drawer title={selRecall ? `Recall ${selRecall.recall_id}` : ""} sub="Reconciliation: located vs retrieved vs customer returns"
              open={!!selRecall} onClose={() => setSelRecall(null)}>
        {selRecall && (
          <RecallDetail recall={selRecall} api={api} />
        )}
      </Drawer>

      <Drawer title={selReturn ? `Return ${selReturn.return_id}` : ""} sub="Reverse logistics workflow"
              open={!!selReturn} onClose={() => setSelReturn(null)}>
        {selReturn && (
          <div>
            <div className="row mb">
              <Badge>{selReturn.status}</Badge>
              <div className="spacer" />
              <span className="small muted">{selReturn.reason}</span>
            </div>
            <Table rows={selReturn.lines || []} empty="No lines"
              columns={[
                { key: "sku", label: "Product" },
                { key: "quantity", label: "Qty" },
                { key: "batch_id", label: "Batch", render: (r) => <span className="mono">{r.batch_id || "—"}</span> },
              ]} />
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
      .then(setRecon).catch((e) => setErr(e.message));
  }, [recall.recall_id]);
  if (err) return <div className="error-box">{err}</div>;
  if (!recon) return <div className="skeleton" style={{ height: 120 }} />;
  return (
    <div>
      <div className="row mb wrap gap">
        <div className="card tinted"><small className="muted">Located (warehouse)</small>
          <div><b>{recon.located_warehouse_qty}</b></div></div>
        <div className="card tinted"><small className="muted">Retrieved</small>
          <div><b>{recon.retrieved_qty}</b></div></div>
        <div className="card tinted"><small className="muted">Destroyed</small>
          <div><b>{recon.destroyed_qty}</b></div></div>
        <div className="card tinted"><small className="muted">Tasks open</small>
          <div><b>{recon.tasks?.open ?? 0} / {recon.tasks?.total ?? 0}</b></div></div>
      </div>
      {recon.outstanding && (
        <div className="error-box mb">⚠ {recon.tasks.open} tasks still open — recall cannot close until contained.</div>
      )}
      <h4 className="mt">Customer returns raised</h4>
      <Table rows={recon.customer_returns || []} empty="No customer returns linked"
        columns={[
          { key: "order_id", label: "Order", render: (r) => <span className="mono">{r.order_id}</span> },
          { key: "return_id", label: "Return", render: (r) => <span className="mono">{r.return_id || "—"}</span> },
          { key: "error", label: "Issue", render: (r) => r.error ? <span className="small text-red">{r.error}</span> : "—" },
        ]} />
      <h4 className="mt">In-transit shipments</h4>
      <div>{(recon.in_transit_shipments || []).map((s) => (
        <span className="mono small chip" key={s}>{s}</span>
      )) || <span className="muted">None</span>}</div>
    </div>
  );
}
