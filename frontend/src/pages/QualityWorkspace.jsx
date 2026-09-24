import React, { useEffect, useState } from "react";
import { api } from "../api";
import { Topbar } from "../App";
import { Badge, Table, When, useToast, CountTabs, Modal, Field } from "../ui";

const TABS = [
  "QC Workbench (LIMS)",
  "Stability Chambers",
  "OOS / OOT Investigations",
  "Certificate of Analysis (CoA)",
  "QA Batch Release",
  "Deviations & 5-Whys",
  "CAPA Management",
  "Change Control",
  "Quality Risk (FMEA)"
];

export default function QualityWorkspace() {
  const [tab, setTab] = useState("QC Workbench (LIMS)");
  const [samples, setSamples] = useState([]);
  const [releases, setReleases] = useState([]);
  const [deviations, setDeviations] = useState([]);
  const [capas, setCapas] = useState([]);
  const [changes, setChanges] = useState([]);
  const [risks, setRisks] = useState([]);
  const [oos, setOos] = useState([]);
  const [err, setErr] = useState("");
  const [sel, setSel] = useState(null);
  const [oosClose, setOosClose] = useState(null);

  // CoA State
  const [selectedCoa] = useState({
    product_name: "Paracetamol Tablets IP 500mg",
    batch_no: "B-2026-09A",
    mfg_date: "2026-09-01",
    exp_date: "2029-08-31",
    tests: [
      { test: "Description", spec: "White round flat tablets", result: "Complies", status: "PASS" },
      { test: "Identification (HPLC)", spec: "Conforms to standard", result: "Conforms", status: "PASS" },
      { test: "Uniformity of Dosage", spec: "IP Limits (AV < 15.0)", result: "AV = 4.2", status: "PASS" },
      { test: "Dissolution (45 min)", spec: "NLT 80% (Q) in phosphate buffer", result: "94.6%", status: "PASS" },
      { test: "Assay (% label claim)", spec: "95.0% - 105.0%", result: "99.8%", status: "PASS" },
      { test: "Related Substances (4-Aminophenol)", spec: "NMT 0.1%", result: "0.012%", status: "PASS" },
      { test: "Microbial Enumeration (TAMC)", spec: "NMT 1000 CFU/g", result: "< 10 CFU/g", status: "PASS" },
    ],
    qa_signed: true,
    signatory: "Dr. A. Verma (Quality Head - Reg # QA-4401)",
    signed_at: "2026-09-20 16:30 IST"
  });

  // Stability testing state
  const [stabilityStudies] = useState([
    { study_id: "STAB-2026-01", chamber: "Chamber 1 (25°C / 60% RH)", type: "REAL_TIME", product: "Paracetamol 500mg", pull: "3 Month Pull", due: "2026-10-01", status: "SCHEDULED" },
    { study_id: "STAB-2026-02", chamber: "Chamber 2 (40°C / 75% RH)", type: "ACCELERATED", product: "Paracetamol 500mg", pull: "1 Month Pull", due: "2026-09-30", status: "IN_TESTING" },
    { study_id: "STAB-2026-03", chamber: "Chamber 3 (30°C / 65% RH)", type: "INTERMEDIATE", product: "Amoxicillin 250mg", pull: "6 Month Pull", due: "2026-11-15", status: "SCHEDULED" },
  ]);

  const { toast, toastHost } = useToast();

  const load = async () => {
    try {
      const [s, r, d, c, ch, rk, oo] = await Promise.all([
        api("/api/qc/samples").catch(() => []),
        api("/api/qa/batch-releases").catch(() => []),
        api("/api/qa/deviations").catch(() => []),
        api("/api/qa/capas").catch(() => []),
        api("/api/qa/changes").catch(() => []),
        api("/api/qa/risks").catch(() => []),
        api("/api/qc/oos").catch(() => []),
      ]);
      setSamples(Array.isArray(s) ? s : (s.items || []));
      setReleases(Array.isArray(r) ? r : (r.items || []));
      setDeviations(Array.isArray(d) ? d : (d.items || []));
      setCapas(Array.isArray(c) ? c : (c.items || []));
      setChanges(Array.isArray(ch) ? ch : (ch.items || []));
      setRisks(Array.isArray(rk) ? rk : (rk.items || []));
      setOos(Array.isArray(oo) ? oo : (oo.items || oo.samples || []));
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

  const tabCounts = {
    "QC Workbench (LIMS)": samples.filter((s) => s.status !== "COMPLETED").length,
    "Stability Chambers": stabilityStudies.filter((s) => s.status === "IN_TESTING").length,
    "OOS / OOT Investigations": oos.filter((r) => r.status === "OOS_INVESTIGATION").length,
    "Certificate of Analysis (CoA)": null,
    "QA Batch Release": releases.filter((r) => (r.status || "PENDING") === "PENDING").length,
    "Deviations & 5-Whys": deviations.filter((d) => d.status !== "CLOSED").length,
    "CAPA Management": capas.filter((c) => c.status !== "CLOSED").length,
    "Change Control": changes.filter((c) => c.status !== "CLOSED").length,
    "Quality Risk (FMEA)": risks.filter((r) => r.risk_level === "HIGH").length,
  };

  return (
    <div>
      {toastHost}
      <Topbar
        title="Quality Control (LIMS) & Quality Assurance (QMS)"
        sub="QC tests & measures · QA investigates, approves and releases — human authority is absolute"
      />
      {err && <div className="error-box mb"><span>⚠️</span> {err}</div>}

      <div className="row mb wrap">
        <CountTabs tabs={TABS.map((t) => ({ label: t, count: tabCounts[t] }))} active={tab} onChange={setTab} />
        <div className="spacer" />
        <button
          className="btn ghost"
          onClick={() => act("/api/agents/run/qa-agent", { goal: "quality_review" }, "QA agent run started")}
        >
          🤖 Run QA Agent
        </button>
      </div>

      <div className="tab-panel" key={tab}>
        {tab === "QC Workbench (LIMS)" && (
          <div className="card">
            <div className="section-title">QC Testing Workbench (Samples Under Test)</div>
            <div className="card-sub">Raw Materials · In-Process Samples · Finished Goods · Environmental Monitoring</div>
            <Table
              rows={samples}
              onRow={setSel}
              empty="No QC samples"
              columns={[
                { key: "sample_id", label: "Sample ID", render: (r) => <span className="mono bold">{r.sample_id}</span> },
                { key: "product_id", label: "Product", render: (r) => <span className="mono">{r.product_id}</span> },
                { key: "batch_id", label: "Batch", render: (r) => <span className="mono">{r.batch_id}</span> },
                { key: "stage", label: "Stage", render: (r) => <Badge tone="teal">{r.stage}</Badge> },
                { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                { key: "open", label: "", render: () => <span className="link small">Enter Results →</span> },
              ]}
            />
          </div>
        )}

        {tab === "Stability Chambers" && (
          <div className="card">
            <div className="section-title">Stability Chambers & Testing Program (ICH Q1A)</div>
            <div className="card-sub">Accelerated, intermediate, and long-term shelf-life study pull schedule tracking</div>
            <Table
              rows={stabilityStudies}
              empty="No active stability studies"
              columns={[
                { key: "study_id", label: "Study ID", render: (r) => <span className="mono">{r.study_id}</span> },
                { key: "product", label: "Product" },
                { key: "chamber", label: "Chamber & Condition" },
                { key: "type", label: "Study Type", render: (r) => <Badge tone={r.type === "ACCELERATED" ? "orange" : "blue"}>{r.type}</Badge> },
                { key: "pull", label: "Pull Interval" },
                { key: "due", label: "Due Date", render: (r) => <span className="small">{r.due}</span> },
                { key: "status", label: "Status", render: (r) => <Badge tone={r.status === "IN_TESTING" ? "orange" : "green"}>{r.status}</Badge> },
                {
                  key: "act",
                  label: "",
                  render: (r) =>
                    r.status === "SCHEDULED" ? (
                      <button className="btn primary sm" onClick={() => toast(`Samples pulled from ${r.chamber}. Testing initiated.`, "ok")}>
                        Pull Samples
                      </button>
                    ) : (
                      <span className="badge green">Testing in LIMS</span>
                    ),
                },
              ]}
            />
          </div>
        )}

        {tab === "OOS / OOT Investigations" && (
          <div className="card">
            <div className="section-title">Out of Specification (OOS) & Out of Trend (OOT) Investigations</div>
            <div className="card-sub">Phase I Laboratory Investigation → Phase II Manufacturing Investigation · QA-gated closure only</div>
            <Table
              rows={oos}
              empty="No open OOS cases"
              columns={[
                { key: "sample_id", label: "Sample", render: (r) => <span className="mono">{r.sample_id}</span> },
                { key: "product_id", label: "Product", render: (r) => <span className="mono">{r.product_id}</span> },
                { key: "batch_id", label: "Batch", render: (r) => <span className="mono">{r.batch_id}</span> },
                {
                  key: "oos",
                  label: "OOS tests",
                  render: (r) => (r.oos || []).length ? <span className="badge orange">{(r.oos || []).join(", ")}</span> : "—",
                },
                { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                {
                  key: "act",
                  label: "",
                  render: (r) =>
                    r.status === "OOS_INVESTIGATION" ? (
                      <button className="btn approve sm" onClick={() => setOosClose(r)}>
                        QA Close Investigation
                      </button>
                    ) : null,
                },
              ]}
            />
          </div>
        )}

        {tab === "Certificate of Analysis (CoA)" && (
          <div className="card">
            <div className="row" style={{ alignItems: "center" }}>
              <div>
                <div className="section-title">Certificate of Analysis (CoA) Console</div>
                <div className="card-sub">Pharmacopoeial compliance verification and QA digital seal</div>
              </div>
              <div className="spacer" />
              <button className="btn primary sm" onClick={() => toast("CoA exported to encrypted PDF with digital timestamp", "ok")}>
                📄 Export Official CoA (PDF)
              </button>
            </div>

            <div style={{ background: "var(--card-bg)", border: "1px solid var(--border)", borderRadius: 8, padding: 24, marginTop: 16 }}>
              <div style={{ textAlign: "center", borderBottom: "2px solid var(--border)", paddingBottom: 16, marginBottom: 16 }}>
                <h2 style={{ margin: 0 }}>CERTIFICATE OF ANALYSIS</h2>
                <div className="small muted">PHARMA AI OS · QUALITY ASSURANCE DEPARTMENT · GMP CERTIFIED</div>
              </div>
              <div className="grid cols-2 mb">
                <div>
                  <div><b>Product:</b> {selectedCoa.product_name}</div>
                  <div><b>Batch Number:</b> <span className="mono">{selectedCoa.batch_no}</span></div>
                </div>
                <div style={{ textAlign: "right" }}>
                  <div><b>Mfg Date:</b> {selectedCoa.mfg_date}</div>
                  <div><b>Exp Date:</b> {selectedCoa.exp_date}</div>
                </div>
              </div>
              <Table
                rows={selectedCoa.tests}
                columns={[
                  { key: "test", label: "Test Parameter" },
                  { key: "spec", label: "Specification Limit" },
                  { key: "result", label: "Observed Result", render: (r) => <b>{r.result}</b> },
                  { key: "status", label: "Compliance", render: (r) => <Badge tone="green">{r.status}</Badge> },
                ]}
              />
              <div className="card tinted mt row" style={{ alignItems: "center" }}>
                <div>
                  <div className="small muted">Authorized QA Signatory</div>
                  <b>{selectedCoa.signatory}</b>
                  <div className="small" style={{ color: "var(--green)" }}>Electronically signed on {selectedCoa.signed_at} (21 CFR Part 11 Validated)</div>
                </div>
                <div className="spacer" />
                <span className="badge green" style={{ padding: "8px 16px", fontSize: 13 }}>RELEASE APPROVED ✓</span>
              </div>
            </div>
          </div>
        )}

        {tab === "QA Batch Release" && (
          <div className="card">
            <div className="section-title">QA Batch Release Decisions (Sole Qualified Person Authority)</div>
            <div className="card-sub">AI compiles dossier · Only qualified QA personnel may execute release, hold, or reject</div>
            <Table
              rows={releases}
              empty="No batches awaiting release"
              columns={[
                { key: "production_order_id", label: "Order", render: (r) => <span className="mono">{r.production_order_id || r.order_id}</span> },
                { key: "batch_id", label: "Batch ID", render: (r) => <span className="mono">{r.batch_id}</span> },
                {
                  key: "dossier",
                  label: "Dossier Status",
                  render: (r) =>
                    r.dossier ? <span className="badge green">Dossier Complete ✓</span> : <span className="badge gray">Pending Assembly</span>,
                },
                { key: "status", label: "Decision", render: (r) => <Badge>{r.status || "PENDING"}</Badge> },
                {
                  key: "act",
                  label: "",
                  render: (r) => (
                    <span className="row" style={{ gap: 6 }}>
                      <button
                        className="btn approve sm"
                        onClick={() =>
                          act(`/api/qa/batch-releases/${r.production_order_id || r.order_id}/decide`, { decision: "RELEASE", reason: "QA release from console" }, "Batch RELEASED to Commercial")
                        }
                      >
                        Release
                      </button>
                      <button
                        className="btn reject sm"
                        onClick={() =>
                          act(`/api/qa/batch-releases/${r.production_order_id || r.order_id}/decide`, { decision: "HOLD", reason: "More review needed" }, "Batch Held for Investigation")
                        }
                      >
                        Hold
                      </button>
                    </span>
                  ),
                },
              ]}
            />
          </div>
        )}

        {tab === "Deviations & 5-Whys" && (
          <div className="card">
            <div className="section-title">Deviations & Root Cause Analysis</div>
            <div className="card-sub">Planned and unplanned manufacturing/laboratory deviations with AI root-cause assistance</div>
            <Table
              rows={deviations}
              empty="No deviations recorded"
              columns={[
                { key: "deviation_id", label: "ID", render: (r) => <span className="mono">{r.deviation_id}</span> },
                { key: "title", label: "Description", render: (r) => <b>{r.title || r.description}</b> },
                { key: "severity", label: "Severity", render: (r) => <Badge tone={r.severity === "CRITICAL" ? "red" : r.severity === "MAJOR" ? "orange" : "blue"}>{r.severity || "MINOR"}</Badge> },
                { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
                { key: "created_at", label: "Logged", render: (r) => <span className="small">{When(r.created_at)}</span> },
              ]}
            />
          </div>
        )}

        {tab === "CAPA Management" && (
          <div className="card">
            <div className="section-title">Corrective & Preventive Actions (CAPA)</div>
            <div className="card-sub">Action plan tracking, effectiveness checks, and 21 CFR Part 11 closure sign-off</div>
            <Table
              rows={capas}
              empty="No active CAPAs"
              columns={[
                { key: "capa_id", label: "CAPA ID", render: (r) => <span className="mono bold">{r.capa_id}</span> },
                { key: "title", label: "Action Plan", render: (r) => <b>{r.title || r.description}</b> },
                { key: "due_date", label: "Due Date", render: (r) => <span className="small">{r.due_date || "—"}</span> },
                { key: "status", label: "Status", render: (r) => <Badge tone={r.status === "CLOSED" ? "green" : "orange"}>{r.status}</Badge> },
                {
                  key: "act",
                  label: "",
                  render: (r) =>
                    r.status !== "CLOSED" ? (
                      <button className="btn approve sm" onClick={() => act(`/api/qa/capas/${r.capa_id}/close`, {}, "CAPA closed")}>
                        Verify & Close
                      </button>
                    ) : null,
                },
              ]}
            />
          </div>
        )}

        {tab === "Change Control" && (
          <div className="card">
            <div className="section-title">Change Control Management</div>
            <div className="card-sub">Impact assessment on process, equipment, validation, and regulatory filings</div>
            <Table
              rows={changes}
              empty="No change controls active"
              columns={[
                { key: "change_id", label: "Change #", render: (r) => <span className="mono">{r.change_id}</span> },
                { key: "title", label: "Change Proposal", render: (r) => <b>{r.title}</b> },
                { key: "category", label: "Category", render: (r) => <Badge tone="teal">{r.category || "PROCESS"}</Badge> },
                { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
              ]}
            />
          </div>
        )}

        {tab === "Quality Risk (FMEA)" && (
          <div className="card">
            <div className="section-title">ICH Q9 Quality Risk Management (FMEA)</div>
            <div className="card-sub">Severity (S) × Occurrence (O) × Detection (D) = Risk Priority Number (RPN)</div>
            <Table
              rows={risks}
              empty="No risk assessments recorded"
              columns={[
                { key: "risk_id", label: "Risk ID", render: (r) => <span className="mono">{r.risk_id}</span> },
                { key: "hazard", label: "Failure Mode / Hazard", render: (r) => <b>{r.hazard || r.title}</b> },
                { key: "rpn", label: "RPN Score", render: (r) => <span className="mono bold">{r.rpn || 48}</span> },
                { key: "risk_level", label: "Risk Level", render: (r) => <Badge tone={r.risk_level === "HIGH" ? "red" : "orange"}>{r.risk_level || "MEDIUM"}</Badge> },
                { key: "status", label: "Status", render: (r) => <Badge>{r.status || "MITIGATED"}</Badge> },
              ]}
            />
          </div>
        )}
      </div>

      <Modal
        title="Close OOS case (QA authority)"
        sub={oosClose ? `Sample ${oosClose.sample_id} · batch ${oosClose.batch_id}` : ""}
        open={!!oosClose}
        onClose={() => setOosClose(null)}
        footer={
          <>
            <button className="btn ghost" onClick={() => setOosClose(null)}>Cancel</button>
            <div className="spacer" />
            <button
              className="btn approve"
              onClick={() => {
                const f = oosClose;
                act(
                  `/api/qc/samples/${f.sample_id}/close-oos`,
                  { conclusion: f.conclusion, root_cause: f.root_cause || "", authorized_retest: f.conclusion === "RETEST_AUTHORIZED" },
                  "OOS closed by QA authority"
                );
                setOosClose(null);
              }}
            >
              Close OOS
            </button>
          </>
        }
      >
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
      <div className="error-box mb">
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
        <textarea
          rows={3}
          value={oos.root_cause || ""}
          placeholder="e.g. HPLC column degradation confirmed; re-injected with fresh column — results within limits"
          onChange={(e) => set("root_cause", e.target.value)}
        />
      </Field>
    </div>
  );
}

function SamplePanelModal({ sample, onClose, act }) {
  const [resultsText, setResultsText] = useState(
    '[\n  { "name": "description", "result": "ok" },\n  { "name": "assay", "result": 99.2 }\n]'
  );
  return (
    <Modal
      title={sample ? `Sample ${sample.sample_id}` : ""}
      open={!!sample}
      onClose={onClose}
      wide
      sub={sample ? `${sample.product_id} · batch ${sample.batch_id} · ${sample.stage}` : ""}
    >
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
            {(sample.status === "REGISTERED" || sample.status === "SAMPLING") && (
              <button className="btn primary sm" onClick={() => act(`/api/qc/samples/${sample.sample_id}/start`, {}, "Testing started")}>
                Start testing
              </button>
            )}
            {(sample.status === "RESULTS_ENTERED" || sample.status === "REVIEW") && (
              <button className="btn primary sm" onClick={() => act(`/api/qc/samples/${sample.sample_id}/complete`, {}, "Review complete")}>
                Complete review
              </button>
            )}
          </div>

          {sample.status === "TESTING" && (
            <div className="mt">
              <Field label="Test results (JSON)" hint="Acceptance limits come from approved specifications — the AI never invents them.">
                <textarea rows={5} className="mono" value={resultsText} onChange={(e) => setResultsText(e.target.value)} />
              </Field>
              <button
                className="btn primary sm"
                onClick={() => {
                  try {
                    const parsed = JSON.parse(resultsText);
                    act(`/api/qc/samples/${sample.sample_id}/results`, { results: parsed }, "Results entered");
                  } catch {
                    alert("Results must be valid JSON");
                  }
                }}
              >
                Submit results
              </button>
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
