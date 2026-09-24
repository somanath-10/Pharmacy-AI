import React, { useEffect, useState } from "react";
import { api } from "../api";
import { Topbar } from "../App";
import { Badge, Table, Money, When, useToast, CountTabs, Drawer, Stepper, Field } from "../ui";

const TABS = ["Vendors", "Sourcing", "Contracts", "Purchase Requisitions", "Purchase Orders"];

export default function VendorsWorkspace() {
  const [tab, setTab] = useState("Purchase Orders");
  const [vendors, setVendors] = useState([]);
  const [events, setEvents] = useState([]);
  const [contracts, setContracts] = useState([]);
  const [prs, setPrs] = useState([]);
  const [pos, setPos] = useState([]);
  const [detail, setDetail] = useState(null);
  const [err, setErr] = useState("");
  const { toast, toastHost } = useToast();

  const load = async () => {
    try {
      const [v, ev, ct, pr, po] = await Promise.all([
        api("/api/vendors").catch(() => []),
        api("/api/sourcing/events").catch(() => []),
        api("/api/sourcing/contracts").catch(() => []),
        api("/api/procurement/prs").catch(() => []),
        api("/api/procurement/pos").catch(() => []),
      ]);
      setVendors(Array.isArray(v) ? v : v.items || []);
      setEvents(Array.isArray(ev) ? ev : ev.items || []);
      setContracts(Array.isArray(ct) ? ct : ct.items || []);
      setPrs(Array.isArray(pr) ? pr : pr.items || []);
      setPos(Array.isArray(po) ? po : po.items || []);
      setErr("");
    } catch (e) { setErr(e.message); }
  };
  useEffect(() => { load(); const t = setInterval(load, 15000); return () => clearInterval(t); }, []);

  const act = async (path, body = {}, label = "Done") => {
    try { await api(path, { method: "POST", body }); toast(label, "ok"); load(); }
    catch (e) { toast(e.message, "err"); }
  };

  const poStages = ["DRAFT", "PENDING_APPROVAL", "APPROVED", "SENT", "ACKNOWLEDGED", "RECEIVED", "CLOSED"];

  return (
    <div>
      {toastHost}
      <Topbar title="Vendors & Procurement"
              sub="Vendor lifecycle → sourcing (RFQ · bids · auction · BRA) → contracts/BPA → PR → PO → P2P" />
      {err && <div className="error-box mb">{err}</div>}

      <div className="row mb wrap">
        <CountTabs tabs={TABS.map((t) => ({
          label: t,
          count: t === "Vendors" ? vendors.length : t === "Sourcing" ? events.length
            : t === "Contracts" ? contracts.length : t === "Purchase Requisitions" ? prs.length : pos.length,
        }))} active={tab} onChange={setTab} />
        <div className="spacer" />
        <button className="btn ghost"
                onClick={() => act("/api/agents/run/procurement-agent", { goal: "replenish_shortages" }, "Procurement agent run started")}>
          🤖 Run Procurement Agent
        </button>
      </div>

      <div className="tab-panel" key={tab}>
        {tab === "Vendors" && (
          <div className="card">
            <h3>Vendor lifecycle</h3>
            <div className="card-sub">Registration → documents → qualification → risk → approval (strategic → human queue)</div>
            <Table rows={vendors} empty="No vendors registered"
              columns={[
                { key: "vendor_id", label: "ID", render: (r) => <span className="mono">{r.vendor_id}</span> },
                { key: "name", label: "Vendor" },
                { key: "vendor_type", label: "Type", render: (r) => <Badge>{r.vendor_type}</Badge> },
                { key: "strategic", label: "Strategic", render: (r) => r.strategic ? <span className="badge purple">Strategic</span> : <span className="muted">—</span> },
                { key: "risk_level", label: "Risk", render: (r) => <Badge>{r.risk_level || "—"}</Badge> },
                { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                { key: "act", label: "", render: (r) => ["REQUESTED", "COMMERCIAL_REVIEW", "QA_REVIEW"].includes(r.status) ? (
                  <button className="btn primary sm" onClick={() =>
                    act(`/api/vendors/${r.vendor_id}/approve`, {}, "Vendor approval requested/processed")}>Approve</button>
                ) : (
                  <a className="link small" href={`/workflows?entity=VENDOR:${r.vendor_id}`}>360 →</a>
                )},
              ]} />
          </div>
        )}

        {tab === "Sourcing" && (
          <div className="card">
            <h3>Strategic sourcing events</h3>
            <div className="card-sub">RFQ → qualified bids → (reverse auction) → BRA / award recommendation → contract</div>
            <Table rows={events} empty="No sourcing events"
              columns={[
                { key: "event_id", label: "Event", render: (r) => <span className="mono">{r.event_id}</span> },
                { key: "title", label: "Title" },
                { key: "type", label: "Type", render: (r) => <Badge>{r.type || "RFQ"}</Badge> },
                { key: "bids", label: "Bids", render: (r) => <span className="badge blue">{(r.bids || []).length}</span> },
                { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                { key: "act", label: "", render: (r) => r.status === "BIDDING" ? (
                  <button className="btn primary sm" onClick={() =>
                    act(`/api/sourcing/events/${r.event_id}/complete-bidding`, {}, "Bidding closed — award evaluation")}>Close bidding</button>
                ) : null },
              ]} />
          </div>
        )}

        {tab === "Contracts" && (
          <div className="contract card">
            <h3>Contracts & BPAs</h3>
            <div className="card-sub">POs against contracts auto-price and auto-approve within policy</div>
            <Table rows={contracts} empty="No contracts yet"
              columns={[
                { key: "contract_id", label: "Contract", render: (r) => <span className="mono">{r.contract_id}</span> },
                { key: "vendor_id", label: "Vendor", render: (r) => <span className="mono">{r.vendor_id}</span> },
                { key: "type", label: "Type" },
                { key: "valid_to", label: "Valid until" },
                { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
              ]} />
          </div>
        )}

        {tab === "Purchase Requisitions" && (
          <div className="card">
            <h3>Purchase requisitions</h3>
            <div className="card-sub">Within policy → auto-approved by agents · over limit → decision queue</div>
            <Table rows={prs} empty="No PRs"
              columns={[
                { key: "pr_id", label: "PR", render: (r) => <span className="mono">{r.pr_id}</span> },
                { key: "title", label: "Title" },
                { key: "estimated_amount", label: "Est. value", render: (r) => <Money v={r.estimated_amount} /> },
                { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                { key: "act", label: "", render: (r) => r.status === "DRAFT" ? (
                  <button className="btn primary sm" onClick={() =>
                    act(`/api/procurement/prs/${r.pr_id}/submit`, {}, "PR submitted")}>Submit</button>
                ) : r.status === "APPROVED" ? (
                  <button className="btn primary sm" onClick={() =>
                    act(`/api/procurement/prs/${r.pr_id}/convert`, {}, "PR → PO (auto-sourced)")}>Convert to PO</button>
                ) : null },
              ]} />
          </div>
        )}

        {tab === "Purchase Orders" && (
          <div className="card">
            <h3>Purchase orders (P2P)</h3>
            <div className="card-sub">PO → approval → send → ack → ASN → GRN → QC → 4-way match → payment</div>
            <Table rows={pos} onRow={setDetail} empty="No POs"
              columns={[
                { key: "po_id", label: "PO", render: (r) => <span className="mono">{r.po_id}</span> },
                { key: "vendor_id", label: "Vendor", render: (r) => <span className="mono">{r.vendor_id}</span> },
                { key: "total_amount", label: "Value", render: (r) => <Money v={r.total_amount} /> },
                { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                { key: "approval", label: "Approval", render: (r) => r.approval
                    ? <Badge>{r.approval.type === "HUMAN" ? "HUMAN_QUEUE" : "AUTO"}</Badge> : <span className="muted">—</span> },
                { key: "open", label: "", render: () => <span className="link small">Open →</span> },
              ]} />
          </div>
        )}
      </div>

      <Drawer title={`PO ${detail?.po_id || ""}`} sub="Procure-to-pay lifecycle"
              open={!!detail} onClose={() => setDetail(null)}>
        {detail && <PoBody po={detail} act={act} stages={poStages} />}
      </Drawer>
    </div>
  );
}

function PoBody({ po, act, stages }) {
  const id = po.po_id;
  const st = po.status;
  return (
    <div>
      <div className="row mb">
        <Badge>{st}</Badge>
        <span className="small muted">{po.vendor_id}</span>
        <div className="spacer" />
        <Money v={po.total_amount} />
      </div>
      <div className="section-title">Lifecycle</div>
      <Stepper steps={stages} current={st} />
      <div className="row mt wrap">
        {st === "DRAFT" &&
          <button className="btn primary sm" onClick={() => act(`/api/procurement/pos/${id}/submit`, {}, "PO submitted for approval")}>Submit</button>}
        {st === "APPROVED" &&
          <button className="btn primary sm" onClick={() => act(`/api/procurement/pos/${id}/send`, {}, "PO sent to vendor")}>Send to vendor</button>}
        {st === "SENT" &&
          <button className="btn primary sm" onClick={() => act(`/api/procurement/pos/${id}/ack`, { accepted: true }, "PO acknowledged")}>Acknowledge</button>}
        <div className="spacer" />
        <a className="link small" href={`/workflows?entity=PURCHASE_ORDER:${id}`}>Full workflow →</a>
      </div>
      {po.lines && (
        <>
          <hr className="divider" />
          <div className="section-title">Lines</div>
          <Table head={["Line", "SKU", "Qty", "Unit price", "Received"]}>
            {po.lines.map((l) => (
              <tr key={l.line_no}><td>{l.line_no}</td><td className="mono">{l.sku}</td>
                <td>{l.quantity}</td><td><Money v={l.unit_price} /></td>
                <td>{l.received_qty ?? 0}</td></tr>
            ))}
          </Table>
        </>
      )}
    </div>
  );
}
