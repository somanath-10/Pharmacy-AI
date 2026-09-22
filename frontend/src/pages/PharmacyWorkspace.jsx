import React, { useEffect, useState } from "react";
import { api } from "../api";
import { Topbar } from "../App";
import { Badge, Table, When, useToast } from "../ui";

export default function PharmacyWorkspace() {
  const [rxs, setRxs] = useState([]);
  const [dispenses, setDispenses] = useState([]);
  const [register, setRegister] = useState([]);
  const [err, setErr] = useState("");
  const [sel, setSel] = useState(null);
  const [raw, setRaw] = useState("");
  const toast = useToast();

  const load = async () => {
    try {
      const [r, d, reg] = await Promise.all([
        api("/api/pharmacy/prescriptions").catch(() => []),
        api("/api/pharmacy/dispenses").catch(() => []),
        api("/api/pharmacy/controlled-register").catch(() => []),
      ]);
      setRxs(Array.isArray(r) ? r : r.items || []);
      setDispenses(Array.isArray(d) ? d : d.items || []);
      setRegister(Array.isArray(reg) ? reg : reg.items || []);
      setErr("");
    } catch (e) { setErr(e.message); }
  };
  useEffect(() => { load(); const t = setInterval(load, 15000); return () => clearInterval(t); }, []);

  const act = async (path, body = {}, label = "Done") => {
    try { await api(path, { method: "POST", body }); toast(label); load(); }
    catch (e) { toast(e.message, "err"); }
  };
  const intake = async (e) => {
    e.preventDefault();
    try {
      const r = await api("/api/pharmacy/prescriptions", { method: "POST", body: { raw_text: raw } });
      toast(`Rx ${r.rx_id || ""} extracted — pharmacist review required`);
      setRaw(""); load();
    } catch (ex) { toast(ex.message, "err"); }
  };

  return (
    <div>
      <Topbar title="Pharmacy / Dispensing"
              sub="Prescription Doc AI → compliance rules → registered pharmacist authority → dispense" />
      {err && <div className="error-box mb">{err}</div>}

      <div className="grid" style={{ gridTemplateColumns: "1.7fr 1fr" }}>
        <div className="card">
          <h3>Prescriptions</h3>
          <div className="card-sub">AI extracts and maps — only a registered pharmacist approves (clinical authority)</div>
          <Table rows={rxs} onRow={setSel} empty="No prescriptions"
            columns={[
              { key: "rx_id", label: "Rx", render: (r) => <span className="mono">{r.rx_id}</span> },
              { key: "patient", label: "Patient", render: (r) => r.extraction?.patient || "—" },
              { key: "meds", label: "Medicines", render: (r) => (r.matched || r.extraction?.medicines || []).length },
              { key: "controlled", label: "Controlled", render: (r) => r.compliance?.controlled_substance
                  ? <span className="badge red">SCHEDULE X</span> : "—" },
              { key: "status", label: "Status", render: (r) => <Badge value={r.status} /> },
              { key: "created_at", label: "Intake", render: (r) => When(r.created_at) },
            ]} />
        </div>

        <div className="card">
          <h3>Prescription intake (Doc AI)</h3>
          <div className="card-sub">Paste prescription text — offline AI extracts medicines, strength, frequency</div>
          <form onSubmit={intake}>
            <div className="field">
              <textarea rows={4} value={raw} onChange={(e) => setRaw(e.target.value)}
                        placeholder={"Dr. Mehta\nPatient: Ravi, age 40\nTab Paracetamol 500mg 1-0-1 x 5 days\nTab Codeine Linctus 100ml 1-1-1 x 3 days"} />
            </div>
            <button className="btn primary" style={{ width: "100%" }} disabled={!raw}>
              Extract & register (UPLOADED → EXTRACTED)
            </button>
          </form>
          <div className="demo-creds mt">
            Controlled substances (Schedule X) auto-route to the pharmacist queue
            and every dispense writes to the <b>narcotics register</b>.
          </div>
        </div>
      </div>

      {sel && (
        <div className="card mt">
          <div className="row">
            <h3>{sel.rx_id} — pharmacist review</h3>
            <div className="spacer" />
            <Badge value={sel.status} />
            <button className="btn ghost sm" onClick={() => setSel(null)}>Close</button>
          </div>
          <div className="wf-flow mt">
            {["UPLOADED", "EXTRACTED", "VALIDATED", "PHARMACIST_REVIEW", "APPROVED", "DISPENSED"].map((s, i) => (
              <React.Fragment key={s}>
                {i > 0 && <span className="wf-arrow">→</span>}
                <div className={`wf-node ${s === sel.status ? "active" : ""}`}><b>{s.replace(/_/g, " ")}</b></div>
              </React.Fragment>
            ))}
          </div>
          <div className="grid cols-2 mt">
            <div>
              <b className="small">Compliance flags</b>
              <pre className="mono" style={{ background: "var(--blue-soft)", padding: 12, borderRadius: 10, fontSize: 11.5 }}>
                {JSON.stringify(sel.compliance || {}, null, 2)}
              </pre>
            </div>
            <div>
              <b className="small">Matched drug master</b>
              <Table rows={(sel.matched || []).map((m, i) => ({ ...m, _key: i }))} empty="Not matched yet"
                columns={[
                  { key: "raw", label: "Raw", render: (r) => <span className="small mono">{r.raw?.name} {r.raw?.strength || ""}{r.raw?.strength_unit || ""}</span> },
                  { key: "product_id", label: "Product", render: (r) => <span className="mono">{r.product_id || "—"}</span> },
                  { key: "matched", label: "Match", render: (r) => r.matched
                      ? <span className="badge green">matched</span> : <span className="badge red">unmatched</span> },
                  { key: "schedule", label: "Schedule", render: (r) => <Badge value={r.schedule || "OTC"} /> },
                ]} />
            </div>
          </div>
          <div className="row mt" style={{ flexWrap: "wrap" }}>
            {(sel.status === "VALIDATED" || sel.status === "PHARMACIST_REVIEW" || sel.status === "CLARIFICATION") && (
              <>
                <button className="btn approve" onClick={() =>
                  act(`/api/pharmacy/prescriptions/${sel.rx_id}/review`,
                      { decision: "APPROVE", notes: "Reviewed by pharmacist" }, "Prescription APPROVED")}>✓ Pharmacist approve</button>
                <button className="btn reject" onClick={() =>
                  act(`/api/pharmacy/prescriptions/${sel.rx_id}/review`,
                      { decision: "REJECT", notes: "Not dispensed" }, "Prescription rejected")}>✗ Reject</button>
              </>
            )}
            {sel.status === "APPROVED" &&
              <button className="btn primary" onClick={() =>
                act("/api/pharmacy/dispense", { rx_id: sel.rx_id }, "Dispensed — register updated")}>Dispense (pharmacist)</button>}
          </div>
        </div>
      )}

      <div className="grid cols-2 mt">
        <div className="card">
          <h3>Dispense history</h3>
          <Table rows={dispenses} empty="No dispenses"
            columns={[
              { key: "rx_id", label: "Rx", render: (r) => <span className="mono">{r.rx_id}</span> },
              { key: "lines", label: "Items", render: (r) => (r.lines || []).length },
              { key: "dispensed_by", label: "By", render: (r) => r.dispensed_by?.id || "—" },
              { key: "created_at", label: "At", render: (r) => When(r.created_at) },
            ]} />
        </div>
        <div className="card">
          <h3>Controlled substances register</h3>
          <div className="card-sub">Statutory register — every Schedule X dispense is recorded</div>
          <Table rows={register} empty="No controlled dispensing"
            columns={[
              { key: "rx_id", label: "Rx", render: (r) => <span className="mono">{r.rx_id}</span> },
              { key: "product_id", label: "Drug", render: (r) => <span className="mono">{r.product_id}</span> },
              { key: "quantity", label: "Qty" },
              { key: "created_at", label: "At", render: (r) => When(r.created_at) },
            ]} />
        </div>
      </div>
    </div>
  );
}
