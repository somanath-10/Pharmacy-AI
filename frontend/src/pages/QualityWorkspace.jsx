import React, { useEffect, useState } from "react";
import { api } from "../api";
import { Topbar } from "../App";
import { Badge, Table, When, useToast } from "../ui";

const TABS = ["QC Samples (LIMS)", "Batch Release", "Deviations & CAPA"];

export default function QualityWorkspace() {
  const [tab, setTab] = useState("QC Samples (LIMS)");
  const [samples, setSamples] = useState([]);
  const [releases, setReleases] = useState([]);
  const [deviations, setDeviations] = useState([]);
  const [capas, setCapas] = useState([]);
  const [err, setErr] = useState("");
  const [sel, setSel] = useState(null);
  const toast = useToast();

  const load = async () => {
    try {
      const [s, r, d, c] = await Promise.all([
        api("/api/qc/samples").catch(() => []),
        api("/api/qa/batch-releases").catch(() => []),
        api("/api/qa/deviations").catch(() => []),
        api("/api/qa/capas").catch(() => []),
      ]);
      setSamples(Array.isArray(s) ? s : s.items || []);
      setReleases(Array.isArray(r) ? r : r.items || []);
      setDeviations(Array.isArray(d) ? d : d.items || []);
      setCapas(Array.isArray(c) ? c : c.items || []);
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
      <Topbar title="Quality — QC/LIMS · QA/QMS"
              sub="QC tests & measures · QA reviews, assures and releases — authority stays human" />
      {err && <div className="error-box mb">{err}</div>}
      <div className="row mb" style={{ flexWrap: "wrap" }}>
        {TABS.map((t) => (
          <button key={t} className={`btn ${tab === t ? "primary" : "ghost"}`} onClick={() => setTab(t)}>{t}</button>
        ))}
        <div className="spacer" />
        <button className="btn ghost"
                onClick={() => act("/api/agents/run/qa-agent", { goal: "quality_review" }, "QA agent run started")}>
          🤖 Run QA Agent
        </button>
      </div>

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
                { key: "status", label: "Status", render: (r) => <Badge value={r.status} /> },
              ]} />
          </div>
          <SamplePanel sample={sel} act={act} />
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
              { key: "dossier", label: "Dossier", render: (r) => r.dossier ? "ready ✓" : "—" },
              { key: "status", label: "Status", render: (r) => <Badge value={r.status || "PENDING"} /> },
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
                { key: "severity", label: "Severity", render: (r) => <Badge value={r.severity} /> },
                { key: "status", label: "Status", render: (r) => <Badge value={r.status} /> },
                { key: "created_at", label: "Raised", render: (r) => When(r.created_at) },
              ]} />
          </div>
          <div className="card">
            <h3>CAPAs</h3>
            <Table rows={capas} empty="No CAPAs"
              columns={[
                { key: "capa_id", label: "ID", render: (r) => <span className="mono">{r.capa_id}</span> },
                { key: "title", label: "Title" },
                { key: "owner", label: "Owner" },
                { key: "status", label: "Status", render: (r) => <Badge value={r.status} /> },
                { key: "due_date", label: "Due" },
              ]} />
          </div>
        </div>
      )}
    </div>
  );
}

function SamplePanel({ sample, act }) {
  const [results, setResults] = useState("{}");
  if (!sample) return <div className="card"><div className="empty">Select a sample to run testing</div></div>;
  return (
    <div className="card">
      <div className="row">
        <h3>{sample.sample_id}</h3>
        <div className="spacer" />
        <Badge value={sample.status} />
      </div>
      <div className="card-sub mono">{sample.product_id} · batch {sample.batch_id} · {sample.stage}</div>
      <div className="row" style={{ flexWrap: "wrap" }}>
        {(sample.status === "REGISTERED" || sample.status === "SAMPLING") &&
          <button className="btn primary sm" onClick={() =>
            act(`/api/qc/samples/${sample.sample_id}/start`, {}, "Testing started")}>Start testing</button>}
        {sample.status === "TESTING" && (
          <>
            <input value={results} onChange={(e) => setResults(e.target.value)}
                   className="mono" style={{ flex: 1, border: "1px solid var(--line)",
                   borderRadius: 8, padding: "7px 10px" }}
                   placeholder='[{"name":"assay","result":99.2}]' />
            <button className="btn primary sm" onClick={() => {
              try {
                const parsed = JSON.parse(results);
                act(`/api/qc/samples/${sample.sample_id}/results`,
                    { results: parsed }, "Results entered");
              } catch { act(`/api/qc/samples/${sample.sample_id}/results`,
                    { results: [{ name: "description", result: "ok" },
                                { name: "assay", result: 99.2 }] }, "Results entered"); }
            }}>Submit results</button>
          </>
        )}
        {(sample.status === "RESULTS_ENTERED" || sample.status === "REVIEW") &&
          <button className="btn primary sm" onClick={() =>
            act(`/api/qc/samples/${sample.sample_id}/complete`, {}, "Review complete")}>Complete review</button>}
      </div>
      {sample.results && (
        <pre className="mono" style={{ background: "var(--blue-soft)", padding: 12, borderRadius: 10,
              fontSize: 11.5, overflow: "auto", marginTop: 10 }}>
          {JSON.stringify(sample.results, null, 2)}
        </pre>
      )}
    </div>
  );
}
