import React, { useEffect, useState } from "react";
import { api } from "../api";
import { Topbar } from "../App";
import { Badge, Table, When, useToast, Drawer, Stepper, Field, Kpi } from "../ui";

export default function PharmacyWorkspace() {
  const [rxs, setRxs] = useState([]);
  const [dispenses, setDispenses] = useState([]);
  const [register, setRegister] = useState([]);
  const [err, setErr] = useState("");
  const [sel, setSel] = useState(null);
  const [raw, setRaw] = useState("");
  const [busy, setBusy] = useState(false);
  const { toast, toastHost } = useToast();

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
    try { await api(path, { method: "POST", body }); toast(label, "ok"); load(); }
    catch (e) { toast(e.message, "err"); }
  };
  const intake = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      const r = await api("/api/pharmacy/prescriptions", { method: "POST", body: { raw_text: raw } });
      toast(`Rx ${r.rx_id || ""} extracted — pharmacist review required`, "ok");
      setRaw(""); load();
    } catch (ex) { toast(ex.message, "err"); } finally { setBusy(false); }
  };

  const pendingReview = rxs.filter((r) => ["VALIDATED", "PHARMACIST_REVIEW", "CLARIFICATION"].includes(r.status)).length;
  const controlled = rxs.filter((r) => r.compliance?.controlled_substance).length;

  return (
    <div>
      {toastHost}
      <Topbar title="Pharmacy / Dispensing"
              sub="Prescription Doc AI → compliance rules → registered pharmacist authority → dispense" />
      {err && <div className="error-box mb">{err}</div>}

      <div className="grid kpi-4 mb">
        <Kpi ico="📝" label="Awaiting pharmacist" value={pendingReview} tone={pendingReview ? "orange" : "green"} />
        <Kpi ico="🚨" label="Controlled (Sch. X)" value={controlled} tone="red" />
        <Kpi ico="💊" label="Dispenses logged" value={dispenses.length} tone="blue" />
        <Kpi ico="📕" label="Register entries" value={register.length} tone="purple" />
      </div>

      <div className="grid" style={{ gridTemplateColumns: "1.7fr 1fr" }}>
        <div className="card rise">
          <h3>Prescriptions</h3>
          <div className="card-sub">AI extracts and maps — only a registered pharmacist approves (clinical authority). Click a row to review.</div>
          <Table rows={rxs} onRow={setSel} empty="No prescriptions"
            columns={[
              { key: "rx_id", label: "Rx", render: (r) => <span className="mono">{r.rx_id}</span> },
              { key: "patient", label: "Patient", render: (r) => r.extraction?.patient || "—" },
              { key: "meds", label: "Meds", render: (r) => (r.matched || r.extraction?.medicines || []).length },
              { key: "controlled", label: "Controlled", render: (r) => r.compliance?.controlled_substance
                  ? <span className="badge red">SCHEDULE X</span> : <span className="muted">—</span> },
              { key: "status", label: "Status", render: (r) => <Badge>{r.status}</Badge> },
              { key: "created_at", label: "Intake", render: (r) => <span className="small">{When(r.created_at)}</span> },
              { key: "open", label: "", render: () => <span className="link small">Open →</span> },
            ]} />
        </div>

        <div className="card rise d1">
          <h3>Prescription intake (Doc AI)</h3>
          <div className="card-sub">Paste prescription text — offline AI extracts medicines, strength, frequency</div>
          <form onSubmit={intake}>
            <Field label="Raw prescription">
              <textarea rows={4} value={raw} onChange={(e) => setRaw(e.target.value)}
                        placeholder={"Dr. Mehta\nPatient: Ravi, age 40\nTab Paracetamol 500mg 1-0-1 x 5 days\nTab Codeine Linctus 100ml 1-1-1 x 3 days"} />
            </Field>
            <button className="btn primary" style={{ width: "100%" }} disabled={!raw || busy}>
              {busy && <span className="spin" />} Extract & register
            </button>
          </form>
          <div className="demo-creds mt">
            Controlled substances (Schedule X) auto-route to the pharmacist queue
            and every dispense writes to the <b>narcotics register</b>.
          </div>
        </div>
      </div>

      <Drawer title={sel ? `${sel.rx_id} — pharmacist review` : ""} sub="Clinical authority is always human"
              open={!!sel} onClose={() => setSel(null)} wide>
        {sel && <RxBody sel={sel} act={act} />}
      </Drawer>

      <div className="grid cols-2 mt">
        <div className="card">
          <h3>Dispense history</h3>
          <Table rows={dispenses} empty="No dispenses"
            columns={[
              { key: "rx_id", label: "Rx", render: (r) => <span className="mono">{r.rx_id}</span> },
              { key: "lines", label: "Items", render: (r) => (r.lines || []).length },
              { key: "dispensed_by", label: "By", render: (r) => r.dispensed_by?.id || "—" },
              { key: "created_at", label: "At", render: (r) => <span className="small">{When(r.created_at)}</span> },
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
              { key: "created_at", label: "At", render: (r) => <span className="small">{When(r.created_at)}</span> },
            ]} />
        </div>
      </div>
    </div>
  );
}

function RxBody({ sel, act }) {
  return (
    <div>
      <div className="row mb">
        <Badge>{sel.status}</Badge>
        {sel.compliance?.controlled_substance && <span className="badge red">SCHEDULE X</span>}
      </div>
      <div className="section-title">Workflow</div>
      <Stepper steps={["UPLOADED", "EXTRACTED", "VALIDATED", "PHARMACIST_REVIEW", "APPROVED", "DISPENSED"]}
               current={sel.status} />
      <div className="grid cols-2 mt">
        <div>
          <div className="section-title">Compliance flags</div>
          <pre className="json-box">{JSON.stringify(sel.compliance || {}, null, 2)}</pre>
        </div>
        <div>
          <div className="section-title">Matched drug master</div>
          <Table rows={(sel.matched || []).map((m, i) => ({ ...m, _key: i }))} empty="Not matched yet"
            columns={[
              { key: "raw", label: "Raw", render: (r) => <span className="small mono">{r.raw?.name} {r.raw?.strength || ""}{r.raw?.strength_unit || ""}</span> },
              { key: "product_id", label: "Product", render: (r) => <span className="mono">{r.product_id || "—"}</span> },
              { key: "matched", label: "Match", render: (r) => r.matched
                  ? <span className="badge green">matched</span> : <span className="badge red">unmatched</span> },
              { key: "schedule", label: "Schedule", render: (r) => <Badge>{r.schedule || "OTC"}</Badge> },
            ]} />
        </div>
      </div>
      <div className="row mt wrap">
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
        <div className="spacer" />
        <a className="link small" href={`/workflows?entity=PRESCRIPTION:${sel.rx_id}`}>Full workflow →</a>
      </div>
    </div>
  );
}
