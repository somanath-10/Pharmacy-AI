import React, { useEffect, useState } from "react";
import { api } from "../api";
import { Topbar } from "../App";
import { Badge, Table, When, useToast, CountTabs, Modal, Field, ErrorBox, toast } from "../ui";

const TABS = ["QC Samples (LIMS)", "OOS / OOT", "Batch Release", "Deviations & CAPA",
  "Change Control", "Risk & Compliance"];

export default function QualityWorkspace() {
  const [tab, setTab] = useState("QC Samples (LIMS)");
  const [samples, setSamples] = useState([]);
  const [releases, setReleases] = useState([]);
  const [deviations, setDeviations] = useState([]);
  const [capas, setCapas] = useState([]);
  const [overdue, setOverdue] = useState(null);
  const [changes, setChanges] = useState([]);
  const [risks, setRisks] = useState([]);
  const [oos, setOos] = useState([]);
  const [err, setErr] = useState("");
  const [sel, setSel] = useState(null);
  const [oosClose, setOosClose] = useState(null);
  const { toast, toastHost } = useToast();

  const load = async () => {
    try {
      const [s, r, d, c, ov, ch, rk, oo] = await Promise.all([
        api("/api/qc/samples").catch(() => []),
        api("/api/qa/batch-releases").catch(() => []),
        api("/api/qa/deviations").catch(() => []),
        api("/api/qa/capas").catch(() => []),
        api("/api/qa/capas/overdue").catch(() => null),
        api("/api/qa/changes").catch(() => []),
        api("/api/qa/risks").catch(() => []),
        api("/api/qc/oos").catch(() => []),
      ]);
      setSamples(Array.isArray(s) ? s : s.items || []);
      setReleases(Array.isArray(r) ? r : r.items || []);
      setDeviations(Array.isArray(d) ? d : d.items || []);
      setCapas(Array.isArray(c) ? c : c.items || []);
      setOverdue(ov);
      setChanges(Array.isArray(ch) ? ch : ch.items || []);
      setRisks(Array.isArray(rk) ? rk : rk.items || []);
      setOos(Array.isArray(oo) ? oo : oo.items || oo.samples || []);
      setErr("");
    } catch (e) { setErr(e.message); }
  };
  useEffect(() => { load(); const t = setInterval(load, 15000); return () => clearInterval(t); }, []);

  const act = async (path, body = {}, label = "Done") => {
    try { await api(path, { method: "POST", body }); toast(label, "ok"); load(); }
    catch (e) { toast(e.message, "err"); }
  };

  const tabCounts = {
    "QC Samples (LIMS)": samples.filter((s) => s.status !== "COMPLETED").length,
    "OOS / OOT": oos.filter((r) => r.status === "OOS_INVESTIGATION").length,
    "Batch Release": releases.filter((r) => (r.status || "PENDING") === "PENDING").length,
    "Deviations & CAPA": capas.filter((c) => c.status !== "CLOSED").length,
    "Change Control": changes.filter((c) => c.status !== "CLOSED").length,
    "Risk & Compliance": risks.filter((r) => r.risk_level === "HIGH").length,
  };

  return (
    <div>
      {toastHost}
      <Topbar title="Quality — QC/LIMS · QA/QMS"
              sub="QC tests & measures · QA reviews, assures and releases — authority stays human" />
      {err && <div className="error-box mb">{err}</div>}

      <div className="row mb wrap">
        <CountTabs tabs={TABS.map((t) => ({ label: t, count: tabCounts[t] }))} active={tab} onChange={setTab} />
        <div className="spacer" />
        <button className="btn ghost"
                onClick={() => act("/api/agents/run/qa-agent", { goal: "quality_review" }, "QA agent run started")}>
          🤖 Run QA Agent
        </button>
      </div>

      <div className="tab-panel" key={tab}>
        {tab === "QC Samples (LIMS)" && (
          <div className="grid" style={{ gridTemplateColumns: "1.6fr 1fr" }}>
            <div className="card">
              <h3>Samples under test</h3>
              <div className="card-sub">Sampling → testing → results → review → completed (specs from approved master data)</div>
              <Table rows={samples} onRow={setSel} empty="No QC samples"
                columns={[
                  { key: "sample_id", label: "Sample", render: (r) => <span className="mono">{r.sample_id}</span> },
                  { key: "product_id", label: "Product", render: (r) => <span className="mono">{r.product_id}</span> },
                  { key: "batch_id", label: "Batch", render: (r) => <span className="mono">{r.batch_id}</span> },
                  { key: "stage", label: "Stage" },
                  { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                  { key: "open", label: "", render: () => <span className="link small">Open →</span> },
                ]} />
            </div>
            <SamplePanel sample={sel} act={act} />
          </div>
        )}

        {tab === "OOS / OOT" && (
          <div className="card">
            <h3>OOS investigations (QA-gated closure)</h3>
            <div className="card-sub">QC investigates · only QA authority closes with a conclusion — AI never closes OOS</div>
            <Table rows={oos} empty="No open OOS cases"
              columns={[
                { key: "sample_id", label: "Sample", render: (r) => <span className="mono">{r.sample_id}</span> },
                { key: "product_id", label: "Product", render: (r) => <span className="mono">{r.product_id}</span> },
                { key: "batch_id", label: "Batch", render: (r) => <span className="mono">{r.batch_id}</span> },
                { key: "oos", label: "OOS tests", render: (r) => (r.oos || []).length
                    ? <span className="badge orange">{(r.oos || []).join(", ")}</span> : "—" },
                { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                { key: "act", label: "", render: (r) => r.status === "OOS_INVESTIGATION" ? (
                  <button className="btn approve sm" onClick={() => setOosClose(r)}>QA close</button>
                ) : null },
              ]} />
          </div>
        )}

        {tab === "Batch Release" && (
          <div className="card">
            <h3>Batch release decisions (QA authority)</h3>
            <div className="card-sub">AI assembles the dossier — a qualified human releases or holds the batch</div>
            <Table rows={releases} empty="No batches awaiting release"
              columns={[
                { key: "production_order_id", label: "Batch", render: (r) => <span className="mono">{r.production_order_id || r.order_id}</span> },
                { key: "batch_id", label: "Batch ID", render: (r) => <span className="mono">{r.batch_id}</span> },
                { key: "dossier", label: "Dossier", render: (r) => r.dossier
                    ? <span className="badge green">ready ✓</span> : <span className="badge gray">pending</span> },
                { key: "status", label: "Status", render: (r) => <Badge>{r.status || "PENDING"}</Badge> },
                { key: "act", label: "", render: (r) => (
                  <span className="row" style={{ gap: 6 }}>
                    <button className="btn approve sm" onClick={() =>
                      act(`/api/qa/batch-releases/${r.production_order_id || r.order_id}/decide`,
                          { decision: "RELEASE", reason: "QA release from console" }, "Batch RELEASED")}>Release</button>
                    <button className="btn reject sm" onClick={() =>
                      act(`/api/qa/batch-releases/${r.production_order_id || r.order_id}/decide`,
                          { decision: "HOLD", reason: "More review needed" }, "Batch held")}>Hold</button>
                  </span>
                )},
              ]} />
          </div>
        )}

        {tab === "Deviations & CAPA" && (
          <div className="grid cols-2">
            <div className="card">
              <h3>Deviations</h3>
              <Table rows={deviations} empty="No deviations"
                columns={[
                  { key: "deviation_id", label: "ID", render: (r) => <span className="mono">{r.deviation_id}</span> },
                  { key: "title", label: "Title" },
                  { key: "severity", label: "Severity", render: (r) => <Badge>{r.severity}</Badge> },
                  { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                  { key: "created_at", label: "Raised", render: (r) => <span className="small">{When(r.created_at)}</span> },
                ]} />
            </div>
            <div className="card">
              <div className="row">
                <h3>CAPAs</h3>
                <div className="spacer" />
                {overdue && overdue.overdue_count > 0 &&
                  <span className="badge red">{overdue.overdue_count} overdue</span>}
              </div>
              <Table rows={capas} empty="No CAPAs"
                columns={[
                  { key: "capa_id", label: "ID", render: (r) => <span className="mono">{r.capa_id}</span> },
                  { key: "title", label: "Title" },
                  { key: "owner", label: "Owner" },
                  { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                  { key: "due_date", label: "Due", render: (r) => r.due_date && r.status !== "CLOSED" &&
                      String(r.due_date).slice(0, 10) < new Date().toISOString().slice(0, 10)
                      ? <span className="badge red">{String(r.due_date).slice(0, 10)}</span>
                      : String(r.due_date || "—").slice(0, 10) },
                ]} />
            </div>
          </div>
        )}

        {tab === "Change Control" && (
          <div className="card">
            <h3>Change control (specifications, methods, BOMs, SOPs)</h3>
            <div className="card-sub">Request → impact assessment → QA approval → implementation (versioned) → verification. Approved masters are never silently overwritten.</div>
            <Table rows={changes} empty="No change requests"
              columns={[
                { key: "change_id", label: "ID", render: (r) => <span className="mono">{r.change_id}</span> },
                { key: "category", label: "Category", render: (r) => <Badge>{r.category}</Badge> },
                { key: "title", label: "Title" },
                { key: "target_id", label: "Target", render: (r) => <span className="mono">{r.target_id}</span> },
                { key: "proposed_version", label: "To version" },
                { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                { key: "act", label: "", render: (r) => (
                  <span className="row" style={{ gap: 4 }}>
                    {r.status === "SUBMITTED" &&
                      <button className="btn primary sm" onClick={() =>
                        act(`/api/qa/changes/${r.change_id}/assess`,
                            { assessment: "reviewed", risk_level: "MEDIUM" }, "Assessment recorded")}>Assess</button>}
                    {r.status === "IN_REVIEW" &&
                      <button className="btn approve sm" onClick={() =>
                        act(`/api/qa/changes/${r.change_id}/approve`,
                            { decision: "APPROVED", reason: "QA approval" }, "Approved")}>Approve</button>}
                    {r.status === "APPROVED" &&
                      <button className="btn primary sm" onClick={() =>
                        act(`/api/qa/changes/${r.change_id}/implement`, {}, "Implemented (versioned)")}>Implement</button>}
                    {r.status === "IMPLEMENTED" &&
                      <button className="btn approve sm" onClick={() =>
                        act(`/api/qa/changes/${r.change_id}/verify`,
                            { verification: "verified" }, "Change closed")}>Verify & close</button>}
                  </span>
                )},
              ]} />
          </div>
        )}

        {tab === "Risk & Compliance" && (
          <div className="card">
            <h3>Quality risk assessments (FMEA)</h3>
            <div className="card-sub">RPN = severity × occurrence × detectability — HIGH ≥ 100, MEDIUM ≥ 40</div>
            <Table rows={risks} empty="No risk assessments"
              columns={[
                { key: "risk_id", label: "ID", render: (r) => <span className="mono">{r.risk_id}</span> },
                { key: "subject", label: "Subject", render: (r) => <span className="mono">{r.subject_type}:{r.subject_id}</span> },
                { key: "severity", label: "S" }, { key: "occurrence", label: "O" }, { key: "detectability", label: "D" },
                { key: "rpn", label: "RPN", render: (r) => <b style={{ color: r.risk_level === "HIGH" ? "var(--red)" : r.risk_level === "MEDIUM" ? "var(--orange)" : "var(--green)" }}>{r.rpn}</b> },
                { key: "risk_level", label: "Level", render: (r) => <Badge>{r.risk_level}</Badge> },
                { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
              ]} />
          </div>
        )}
      </div>

      {/* OOS close modal */}
      <Modal title="Close OOS case (QA authority)" sub={oosClose ? `Sample ${oosClose.sample_id} · batch ${oosClose.batch_id}` : ""}
             open={!!oosClose} onClose={() => setOosClose(null)}
             footer={
               <>
                 <button className="btn ghost" onClick={() => setOosClose(null)}>Cancel</button>
                 <div className="spacer" />
                 <button className="btn approve" onClick={() => {
                   const f = oosClose;
                   act(`/api/qc/samples/${f.sample_id}/close-oos`,
                       { conclusion: f.conclusion, root_cause: f.root_cause || "",
                         authorized_retest: f.conclusion === "RETEST_AUTHORIZED" },
                       "OOS closed by QA authority");
                   setOosClose(null);
                 }}>
                   Close OOS
                 </button>
               </>
             }>
        <OosCloseBody oos={oosClose} onChange={setOosClose} />
      </Modal>

      <SamplePanelModal sample={sel} onClose={() => setSel(null)} act={act} />
    </div>
  );
}

function OosCloseBody({ oos, onChange }) {
  if (!oos) return null;
  const set = (k, v) => onChange({ ...oos, [k]: v });
  return (
    <div>
      <div className="error-box">
        <span>⚠️</span>
        <span>AI never closes OOS autonomously. Your conclusion and root cause are recorded in the audit trail.</span>
      </div>
      <Field label="Conclusion">
        <select value={oos.conclusion || ""} onChange={(e) => set("conclusion", e.target.value)}>
          <option value="">Select conclusion…</option>
          <option value="CONFIRMED_OOS">CONFIRMED_OOS — batch material affected</option>
          <option value="LAB_ERROR">LAB_ERROR — analytical error, result invalid</option>
          <option value="RETEST_AUTHORIZED">RETEST_AUTHORIZED — authorize retest/resample</option>
        </select>
      </Field>
      <Field label="Root cause / investigation summary">
        <textarea rows={3} value={oos.root_cause || ""}
                  placeholder="e.g. HPLC column degradation confirmed; re-injected with fresh column — results within limits"
                  onChange={(e) => set("root_cause", e.target.value)} />
      </Field>
    </div>
  );
}

function SamplePanelModal({ sample, onClose, act }) {
  const [resultsText, setResultsText] = useState(
    '[\n  { "name": "description", "result": "ok" },\n  { "name": "assay", "result": 99.2 }\n]');
  return (
    <Modal title={sample ? `Sample ${sample.sample_id}` : ""} open={!!sample} onClose={onClose} wide
           sub={sample ? `${sample.product_id} · batch ${sample.batch_id} · ${sample.stage}` : ""}>
      {sample && (
        <div>
          <div className="row mb">
            <Badge>{sample.status}</Badge>
            {sample.oos && sample.oos.length > 0 && <span className="badge orange">OOS: {sample.oos.join(", ")}</span>}
            {sample.oot && sample.oot.length > 0 && <span className="badge blue">OOT trend detected</span>}
          </div>

          <div className="section-title">Test workflow</div>
          <div className="wf-flow">
            {["REGISTERED", "TESTING", "RESULTS_ENTERED", "COMPLETED"].map((s, i) => (
              <React.Fragment key={s}>
                {i > 0 && <span className="wf-arrow">→</span>}
                <div className={`wf-node ${s === sample.status ? "active" : "pending"}`}><b>{s}</b></div>
              </React.Fragment>
            ))}
          </div>

          <div className="row mt wrap">
            {(sample.status === "REGISTERED" || sample.status === "SAMPLING") &&
              <button className="btn primary sm" onClick={() =>
                act(`/api/qc/samples/${sample.sample_id}/start`, {}, "Testing started")}>Start testing</button>}
            {(sample.status === "RESULTS_ENTERED" || sample.status === "REVIEW") &&
              <button className="btn primary sm" onClick={() =>
                act(`/api/qc/samples/${sample.sample_id}/complete`, {}, "Review complete")}>Complete review</button>}
          </div>

          {sample.status === "TESTING" && (
            <div className="mt">
              <Field label="Test results (JSON)" hint="Acceptance limits come from approved specifications — the AI never invents them.">
                <textarea rows={5} className="mono" value={resultsText}
                          onChange={(e) => setResultsText(e.target.value)} />
              </Field>
              <button className="btn primary sm" onClick={() => {
                try {
                  const parsed = JSON.parse(resultsText);
                  act(`/api/qc/samples/${sample.sample_id}/results`, { results: parsed }, "Results entered");
                } catch { toast("Results must be valid JSON", "err"); }
              }}>Submit results</button>
            </div>
          )}

          {sample.results && (
            <>
              <hr className="divider" />
              <div className="section-title">Recorded results</div>
              <pre className="json-box">{JSON.stringify(sample.results, null, 2)}</pre>
            </>
          )}
        </div>
      )}
    </Modal>
  );
}
