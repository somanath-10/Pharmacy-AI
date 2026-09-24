import React, { useEffect, useState } from "react";
import { api, getMe } from "../api";
import { Topbar } from "../App";
import { Badge, Table, When, useToast, Kpi, Skeleton, Modal } from "../ui";

export default function SopTraining() {
  const [data, setData] = useState({ sops: [], sign_offs: [], compliance_pct: 100 });
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [selectedSop, setSelectedSop] = useState(null);
  const me = getMe();
  const { toast, toastHost } = useToast();

  const load = async () => {
    try {
      const res = await api("/api/analytics/sop-training");
      setData(res || { sops: [], sign_offs: [], compliance_pct: 100 });
      setErr("");
    } catch (e) {
      setErr(e.message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const signSop = async (sop) => {
    try {
      await api("/api/analytics/sop-training/sign", {
        method: "POST",
        body: { sop_id: sop.sop_id, title: sop.title },
      });
      toast(`Digitally signed and acknowledged ${sop.sop_id}`, "ok");
      setSelectedSop(null);
      load();
    } catch (e) {
      toast(e.message, "err");
    }
  };

  const sops = data.sops || [];

  return (
    <div>
      {toastHost}
      <Topbar
        title="SOP Training & Qualification Matrix"
        sub="Standard Operating Procedure (SOP) version registry, role qualification rules, and 21 CFR Part 11 compliant digital signature acknowledgements"
      />

      {err && <div className="error-box mb"><span>⚠️</span> {err}</div>}

      <div className="grid kpi-4 mb">
        <Kpi ico="📖" label="Assigned SOPs" value={sops.length} tone="blue" foot="Mandatory for your roles" />
        <Kpi ico="✅" label="Signed & Acknowledged" value={`${data.compliance_pct || 0}%`} tone="green" foot="Current compliance score" />
        <Kpi ico="✍️" label="Pending Signatures" value={sops.filter((s) => !s.signed).length} tone={sops.some((s) => !s.signed) ? "orange" : "green"} foot="Requires digital sign-off" />
        <Kpi ico="🔒" label="Signature Standard" value="21 CFR Part 11" tone="green" foot="Legally binding audit record" />
      </div>

      {loading ? (
        <Skeleton rows={5} />
      ) : (
        <div className="card">
          <div className="row mb">
            <div>
              <h3>Standard Operating Procedure Training Matrix</h3>
              <div className="card-sub">Review active SOPs and submit required digital acknowledgements</div>
            </div>
            <div className="spacer" />
          </div>

          <Table
            columns={[
              { key: "sop_id", label: "SOP ID", render: (r) => <span className="mono bold">{r.sop_id}</span> },
              { key: "title", label: "SOP Title" },
              { key: "version", label: "Version", render: (r) => <span className="badge blue mono">{r.version}</span> },
              { key: "effective_date", label: "Effective", render: (r) => <span className="small">{r.effective_date}</span> },
              {
                key: "signed",
                label: "Qualification Status",
                render: (r) => (
                  <span className={`badge ${r.signed ? "green" : "orange"}`}>
                    {r.signed ? "QUALIFIED / SIGNED" : "SIGN-OFF REQUIRED"}
                  </span>
                ),
              },
              {
                key: "action",
                label: "Electronic Signature",
                render: (r) =>
                  r.signed ? (
                    <span className="small muted">Signed on {When(r.signed_at)}</span>
                  ) : (
                    <button className="btn primary xs" onClick={() => setSelectedSop(r)}>
                      Review & Sign SOP
                    </button>
                  ),
              },
            ]}
            rows={sops}
          />
        </div>
      )}

      {selectedSop && (
        <Modal
          title={`21 CFR Part 11 Electronic Signature: ${selectedSop.sop_id}`}
          sub={selectedSop.title}
          onClose={() => setSelectedSop(null)}
          footer={
            <>
              <button className="btn ghost" onClick={() => setSelectedSop(null)}>Cancel</button>
              <div className="spacer" />
              <button className="btn primary" onClick={() => signSop(selectedSop)}>
                ✍️ Authenticate & Sign SOP
              </button>
            </>
          }
        >
          <div style={{ padding: 10 }}>
            <p className="small mb" style={{ lineHeight: 1.6 }}>
              <b>Standard Operating Procedure Summary:</b> This procedure establishes mandatory compliance protocols for <b>{selectedSop.title}</b> (Version: {selectedSop.version}). Operators, analysts, and supervisors must adhere strictly to these guidelines during all production, laboratory, warehouse, and clinical dispensing activities.
            </p>
            <div className="callout-box" style={{ background: "#f8fafc", padding: 14, borderRadius: 10, border: "1px solid var(--line)" }}>
              <div className="small muted mb">
                <b>Signatory Identity:</b> {me?.name || me?.email} ({me?.user_id})<br />
                <b>Role Qualification:</b> {(me?.roles || []).join(", ")}<br />
                <b>Meaning of Signature:</b> <i>"I hereby certify that I have read, understood, and received adequate training on this SOP, and agree to strictly comply with all procedures described herein."</i>
              </div>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}

