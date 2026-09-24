import React, { useEffect, useState } from "react";
import { api } from "../api";
import { Topbar } from "../App";
import { Badge, Table, When, useToast, CountTabs, Field, Empty } from "../ui";

const CATS = ["ALL", "FINANCIAL_AUTHORITY", "STRATEGIC", "REGULATORY_EXCEPTION",
              "SECURITY_FRAUD", "QA_AUTHORITY", "CLINICAL_AUTHORITY",
              "UNRESOLVED_EXCEPTION"];

function riskTone(ev) {
  if (!ev) return "muted";
  const s = JSON.stringify(ev).toLowerCase();
  if (s.includes("recall") || s.includes("fraud") || s.includes("critical")) return "text-red";
  if (s.includes("high") || s.includes("failed") || s.includes("mismatch")) return "text-orange";
  return "";
}

export default function DecisionQueue() {
  const [rows, setRows] = useState([]);
  const [cat, setCat] = useState("ALL");
  const [sel, setSel] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [reason, setReason] = useState("");
  const [modification, setModification] = useState("");
  const [showModify, setShowModify] = useState(false);
  const { toast, toastHost } = useToast();

  const load = async () => {
    try {
      const r = await api("/api/approvals?status=PENDING");
      setRows(Array.isArray(r) ? r : r.items || []);
      setErr("");
    } catch (e) { setErr(e.message); }
  };
  useEffect(() => { load(); const t = setInterval(load, 10000); return () => clearInterval(t); }, []);

  const decide = async (decision) => {
    if (!sel) return;
    setBusy(true);
    try {
      const body = { decision, reason: reason || `${decision} via Decision Queue` };
      if (decision === "MODIFIED" && modification.trim()) {
        try { body.modification = JSON.parse(modification); }
        catch { toast("Modification must be valid JSON", "err"); setBusy(false); return; }
      }
      await api(`/api/approvals/${sel.approval_id}/decide`, { method: "POST", body });
      toast(`Decision recorded: ${decision.toLowerCase()}`, "ok");
      setSel(null); setReason(""); setModification(""); setShowModify(false);
      load();
    } catch (e) { toast(e.message, "err"); } finally { setBusy(false); }
  };

  const filtered = cat === "ALL" ? rows : rows.filter((r) => r.category === cat);
  const countFor = (c) => (c === "ALL" ? rows.length : rows.filter((r) => r.category === c).length);
  const aiSummary = sel?.evidence?.ai_summary || sel?.evidence?.summary
    || sel?.evidence?.explanation || sel?.evidence?.recommended_action;

  return (
    <div>
      {toastHost}
      <Topbar title="Human Decision Queue"
              sub="One central screen for every human authority decision — agents assemble the evidence and have already tried to self-resolve" />
      {err && <div className="error-box mb">{err}</div>}

      <div className="grid" style={{ gridTemplateColumns: "1.7fr 1fr" }}>
        <div className="card rise">
          <div className="row wrap mb">
            <h3 style={{ marginRight: 6 }}>Pending decisions</h3>
            <div className="spacer" />
          </div>
          <CountTabs
            tabs={CATS.map((c) => ({ label: c, count: countFor(c) }))}
            active={cat} onChange={setCat} />
          <div className="mt" />
          <Table
            empty="Queue is clear — agents have resolved everything"
            rows={filtered}
            onRow={setSel}
            columns={[
              { key: "approval_id", label: "ID", render: (r) => <span className="mono">{r.approval_id}</span> },
              { key: "title", label: "Item", render: (r) => (
                <span>{r.title}
                  {r.escalated && <Badge>ESCALATED</Badge>}
                </span>) },
              { key: "category", label: "Category", render: (r) => <Badge>{r.category}</Badge> },
              { key: "entity_id", label: "Entity", render: (r) => <span className="mono">{r.entity_type} {r.entity_id}</span> },
              { key: "due", label: "SLA", render: (r) => <span className="small">{When(r.due_at)}</span> },
              { key: "act", label: "", render: () => <span className="link small">Review →</span> },
            ]} />
        </div>

        <div className="card rise d1">
          {!sel ? (
            <Empty art="🙋" title="Select an item to review"
                   note="Evidence, risk and AI analysis are assembled here. Self-resolution has already run before escalation." />
          ) : (
            <div className="tab-panel">
              <h3>{sel.title}</h3>
              <div className="card-sub mono">{sel.approval_id} · {sel.entity_type} {sel.entity_id}</div>
              <div className="row mb wrap">
                <Badge>{sel.category}</Badge>
                <Badge>{sel.status}</Badge>
                {sel.escalated && <Badge>ESCALATED</Badge>}
                <span className={`small ${riskTone(sel.evidence)}`}
                      style={{ fontWeight: 700 }}>
                  Risk: {sel.evidence?.risk || sel.risk || (sel.category === "SECURITY_FRAUD" ? "HIGH" : "REVIEW")}
                </span>
              </div>

              {aiSummary && (
                <div className="field">
                  <label>AI analysis / recommendation</label>
                  <div className="card tinted"><span className="small">{aiSummary}</span></div>
                </div>
              )}

              <div className="field">
                <label>Evidence (assembled by AI)</label>
                <pre className="json-box">
                  {JSON.stringify(sel.evidence, null, 2)}
                </pre>
              </div>

              {showModify && (
                <Field label="Modification — JSON patch applied to the request payload (e.g. {&quot;amount&quot;: 40000})">
                  <textarea rows={3} className="mono" value={modification}
                            placeholder='{"amount": 40000}'
                            onChange={(e) => setModification(e.target.value)} />
                </Field>
              )}

              <Field label="Reason (recorded in audit trail)">
                <textarea rows={2} value={reason} placeholder="Why this decision?"
                          onChange={(e) => setReason(e.target.value)} />
              </Field>

              <div className="row mt wrap">
                <button className="btn approve" disabled={busy} onClick={() => decide("APPROVED")}>✓ Approve</button>
                <button className="btn reject" disabled={busy} onClick={() => decide("REJECTED")}>✗ Reject</button>
                <button className="btn primary" disabled={busy}
                        onClick={() => showModify ? decide("MODIFIED") : setShowModify(true)}>
                  ✎ {showModify ? "Apply modification" : "Modify"}
                </button>
                <button className="btn ghost" disabled={busy} onClick={() => decide("ESCALATED")}>⤴ Escalate</button>
                <div className="spacer" />
                <button className="btn ghost sm" onClick={() => { setSel(null); setShowModify(false); }}>Close</button>
              </div>

              <a className="link small mt" href={`/workflows?entity=${sel.entity_type}:${sel.entity_id}`}>
                View full workflow →
              </a>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
