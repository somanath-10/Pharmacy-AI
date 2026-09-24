import React, { useState } from "react";
import { login } from "../api";
import { Field, ErrorBox } from "../ui";

const FEATURES = [
  ["🧠", "Autonomous AI agents run planning, sourcing, procurement, and warehouse orchestration"],
  ["🧬", "GxP-grade quality: FEFO, quarantine, OOS/OOT, CAPA, change control, full audit trail"],
  ["🔒", "SoD-enforced approvals, MFA-ready auth, short-lived tokens, revocable sessions"],
];

const DEMO_ROLES = [
  ["admin@pharmaos.local", "Super Admin", "SUPER_ADMIN"],
  ["pharmacist@pharmaos.local", "Pharmacist", "PHARMACIST"],
  ["warehouse@pharmaos.local", "Warehouse", "WAREHOUSE"],
  ["plant@pharmaos.local", "Plant Operator", "PLANT"],
  ["qc@pharmaos.local", "QC Analyst", "QC"],
  ["qa@pharmaos.local", "QA Authority", "QA"],
  ["buyer@pharmaos.local", "Buyer", "PROCUREMENT"],
  ["sales@pharmaos.local", "Sales & CRM", "SALES"],
  ["finance@pharmaos.local", "Finance", "FINANCE"],
  ["logistics@pharmaos.local", "Logistics", "LOGISTICS"],
  ["compliance@pharmaos.local", "Compliance", "COMPLIANCE"],
  ["auditor@pharmaos.local", "Auditor", "AUDITOR"],
  ["vendor@acmecorp.com", "Supplier Portal", "SUPPLIER"],
];

export default function Login() {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [mfa, setMfa] = useState("");
  const [showPw, setShowPw] = useState(false);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e) => {
    e.preventDefault();
    setErr("");
    setBusy(true);
    try {
      await login(email.trim(), password, mfa.trim() || undefined);
    } catch (ex) {
      setErr(ex.message || "Login failed");
    } finally {
      setBusy(false);
    }
  };

  const quickFill = (userEmail) => {
    setEmail(userEmail);
    setPassword("Pharma@123");
  };

  return (
    <div className="login-wrap">
      <div className="login-hero">
        <span className="orb" style={{ width: 190, height: 190, top: "12%", left: "8%", animationDelay: "0.6s" }} />
        <span className="orb" style={{ width: 120, height: 120, bottom: "16%", right: "12%", animationDelay: "1.4s" }} />
        <div style={{ position: "relative", zIndex: 1 }}>
          <div className="logo-badge" style={{ width: 46, height: 46, fontSize: 24, borderRadius: 13 }}>
            ✦
          </div>
          <h1>The autonomous <em>Pharma Enterprise OS</em></h1>
          <p>
            AI agents plan supply, source vendors, run procure-to-pay, orchestrate warehouses,
            and police quality — humans only decide what machines should not.
          </p>
          <div style={{ marginTop: 22 }}>
            {FEATURES.map(([ico, text]) => (
              <div className="feat" key={text}>
                <span className="f-ico">{ico}</span>
                <span>{text}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="login-form-side">
        <div className="login-card" style={{ maxWidth: 440 }}>
          <div className="card-inner">
            <div className="logo-badge" style={{ width: 44, height: 44, fontSize: 23 }}>✦</div>
            <h1>Welcome back</h1>
            <div className="sub">Sign in to your Pharma AI OS role workspace.</div>

            <ErrorBox>{err}</ErrorBox>
            <form onSubmit={submit}>
              <Field label="Work email">
                <input
                  type="email" required value={email}
                  placeholder="you@company.com" autoComplete="username"
                  onChange={(e) => setEmail(e.target.value)}
                />
              </Field>
              <Field label="Password">
                <div className="pw-wrap">
                  <input
                    type={showPw ? "text" : "password"} required value={password}
                    placeholder="••••••••" autoComplete="current-password"
                    onChange={(e) => setPassword(e.target.value)}
                  />
                  <button type="button" className="toggle" onClick={() => setShowPw(!showPw)}
                          aria-label="Toggle password visibility">
                    {showPw ? "🙈" : "👁️"}
                  </button>
                </div>
              </Field>
              <Field label="MFA code" hint="Only required if you've enrolled a TOTP device.">
                <input
                  inputMode="numeric" value={mfa}
                  placeholder="6-digit code (optional)"
                  onChange={(e) => setMfa(e.target.value)}
                />
              </Field>
              <button className="btn primary lg" style={{ width: "100%", marginTop: 6 }} disabled={busy}>
                {busy ? <span className="spin" /> : <span>→</span>}
                {busy ? "Signing in…" : "Sign in"}
              </button>
            </form>

            <div className="demo-creds" style={{ marginTop: 20 }}>
              <b>1-Click Demo Logins</b> (Password: <code className="kbd">Pharma@123</code>):
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 8 }}>
                {DEMO_ROLES.map(([userEmail, label, roleCode]) => (
                  <button
                    key={userEmail}
                    type="button"
                    className="btn ghost sm"
                    style={{ fontSize: 11, padding: "4px 8px" }}
                    onClick={() => quickFill(userEmail)}
                    title={`Role: ${roleCode}`}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
