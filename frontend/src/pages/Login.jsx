import React, { useState } from "react";
import { login } from "../api";
import { useToast } from "../ui";

const DEMO = [
  ["admin@pharmaos.local", "Admin@123", "Super Admin"],
  ["buyer@pharmaos.local", "Buyer@123", "Procurement"],
  ["qa@pharmaos.local", "Qa@123", "QA Authority"],
  ["pharmacist@pharmaos.local", "Pharm@123", "Pharmacist"],
  ["finance@pharmaos.local", "Fin@123", "Finance"],
  ["warehouse@pharmaos.local", "Wh@123", "Warehouse"],
];

export default function Login() {
  const [email, setEmail] = useState("admin@pharmaos.local");
  const [password, setPassword] = useState("Admin@123");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const toast = useToast();

  const submit = async (e) => {
    e.preventDefault();
    setBusy(true); setErr("");
    try {
      await login(email, password);
      toast("Welcome to Pharma AI OS");
    } catch (ex) {
      setErr(ex.message);
    } finally { setBusy(false); }
  };

  return (
    <div className="login-wrap">
      <form className="login-card" onSubmit={submit}>
        <div className="logo-badge">✦</div>
        <h1>Pharma AI OS</h1>
        <div className="sub">One autonomous enterprise core — AI does the routine, humans approve what matters.</div>
        {err && <div className="error-box">{err}</div>}
        <div className="field">
          <label>Email</label>
          <input value={email} onChange={(e) => setEmail(e.target.value)} autoFocus />
        </div>
        <div className="field">
          <label>Password</label>
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
        <button className="btn primary" style={{ width: "100%", padding: 11 }} disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        <div className="demo-creds">
          <b>Demo accounts</b> (click to fill)<br />
          {DEMO.map(([em, pw, role]) => (
            <a key={em} href="#" onClick={(e) => { e.preventDefault(); setEmail(em); setPassword(pw); }}
               style={{ display: "inline-block", marginRight: 12, color: "var(--blue)", fontWeight: 600 }}>
              {role}
            </a>
          ))}
        </div>
      </form>
    </div>
  );
}
