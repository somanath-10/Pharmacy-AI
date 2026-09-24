import React, { useEffect, useState } from "react";
import { api, getMe, logout } from "../api";
import { Topbar } from "../App";
import { Badge, Table, When, useToast, Kpi, Skeleton, Modal, Field } from "../ui";

export default function UserProfile() {
  const [profile, setProfile] = useState(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [mfaModal, setMfaModal] = useState(false);
  const [mfaQr, setMfaQr] = useState(null);
  const [totpCode, setTotpCode] = useState("");
  const { toast, toastHost } = useToast();

  const load = async () => {
    try {
      const u = await api("/api/auth/me");
      setProfile(u);
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

  const enrollMfa = async () => {
    try {
      const res = await api("/api/auth/mfa/enroll", { method: "POST" });
      setMfaQr(res);
      setMfaModal(true);
    } catch (e) {
      toast(e.message, "err");
    }
  };

  const activateMfa = async () => {
    try {
      await api("/api/auth/mfa/activate", {
        method: "POST",
        body: { code: totpCode },
      });
      toast("Two-Factor Authentication (TOTP) successfully activated", "ok");
      setMfaModal(false);
      setTotpCode("");
      load();
    } catch (e) {
      toast(e.message, "err");
    }
  };

  const roles = profile?.roles || [];

  return (
    <div>
      {toastHost}
      <Topbar
        title="User Identity & Access Profile"
        sub="Role assignments, Segregation of Duties data scopes, site & warehouse authorization, and multi-factor authentication (MFA)"
      />

      {err && <div className="error-box mb"><span>⚠️</span> {err}</div>}

      {loading ? (
        <Skeleton rows={6} />
      ) : (
        <div className="grid cols-2">
          <div className="card">
            <h3>Identity & Organizational Placement</h3>
            <div className="card-sub">Verified identity credentials and assigned role authorizations</div>

            <div style={{ display: "flex", alignItems: "center", gap: 16, marginBottom: 20 }}>
              <div style={{ width: 56, height: 56, borderRadius: 16, background: "linear-gradient(135deg, #2563eb, #4f46e5)", color: "#fff", display: "grid", placeItems: "center", fontSize: 24, fontWeight: 800 }}>
                {(profile?.name || "U")[0]}
              </div>
              <div>
                <h4 style={{ margin: 0, fontSize: 18, color: "var(--navy)" }}>{profile?.name}</h4>
                <span className="small muted">{profile?.email}</span>
                <div style={{ marginTop: 6 }}>
                  <span className="badge green">{profile?.status || "ACTIVE"}</span>
                </div>
              </div>
            </div>

            <div className="divider" />

            <h4>Assigned Enterprise Roles</h4>
            <div className="tag-list mb">
              {roles.map((r) => (
                <span key={r} className="badge blue bold" style={{ fontSize: 13, padding: "5px 12px" }}>
                  🛡️ {r}
                </span>
              ))}
            </div>

            <div className="divider" />

            <h4>Data Access Scopes (ABAC)</h4>
            <div className="stat-line">
              <div className="st-b"><small>Site Scope</small><b>{profile?.site_ids ? profile.site_ids.join(", ") : "GLOBAL (All Sites)"}</b></div>
              <div className="st-b"><small>Warehouse Scope</small><b>All Authorized Zones</b></div>
              <div className="st-b"><small>Party Isolation</small><b>{profile?.vendor_id || profile?.customer_id || "Internal Staff"}</b></div>
            </div>
          </div>

          <div className="card">
            <h3>Security & Authentication Settings</h3>
            <div className="card-sub">Multi-Factor Authentication, Session Lifespan, and Cryptographic Security</div>

            <div style={{ padding: 14, background: "#f8fafc", borderRadius: 12, border: "1px solid var(--line)", marginBottom: 16 }}>
              <div className="row">
                <div>
                  <b style={{ color: "var(--navy)" }}>Two-Factor Authentication (TOTP)</b>
                  <div className="small muted mt" style={{ marginTop: 4 }}>
                    {profile?.mfa_enrolled ? "✅ Active & Enforced (Authenticator App)" : "⚠️ Not Enrolled. Enable to protect privileged actions."}
                  </div>
                </div>
                <div className="spacer" />
                {!profile?.mfa_enrolled ? (
                  <button className="btn primary sm" onClick={enrollMfa}>
                    Enable MFA
                  </button>
                ) : (
                  <span className="badge green">PROTECTED</span>
                )}
              </div>
            </div>

            <h4>Active Session Details</h4>
            <div className="stat-line mb">
              <div className="st-b"><small>Token Type</small><b>JWT (HMAC-SHA256)</b></div>
              <div className="st-b"><small>Rotation</small><b>Refresh Token Rotation Active</b></div>
              <div className="st-b"><small>Account Lockout Guard</small><b>Active (5 Attempt Limit)</b></div>
            </div>

            <div className="divider" />

            <div className="row">
              <button className="btn danger" onClick={logout}>
                ⎋ Sign Out of Session
              </button>
            </div>
          </div>
        </div>
      )}

      {mfaModal && (
        <Modal
          title="Enroll Two-Factor Authentication"
          sub="Scan QR code using Google Authenticator, 1Password, or any standard TOTP app"
          onClose={() => setMfaModal(false)}
          footer={
            <>
              <button className="btn ghost" onClick={() => setMfaModal(false)}>Cancel</button>
              <div className="spacer" />
              <button className="btn primary" onClick={activateMfa}>Verify & Activate</button>
            </>
          }
        >
          <div style={{ padding: 12, textAlign: "center" }}>
            <p className="small muted mb">Enter the secret key or scan URI into your authenticator app:</p>
            <div className="mono small mb" style={{ background: "#f1f5f9", padding: 10, borderRadius: 8, wordBreak: "break-all" }}>
              {mfaQr?.secret || "JBSWY3DPEHPK3PXP"}
            </div>
            <Field label="6-Digit Verification Code">
              <input
                style={{ textAlign: "center", fontSize: 20, letterSpacing: "0.3em", fontWeight: 700 }}
                maxLength={6}
                value={totpCode}
                onChange={(e) => setTotpCode(e.target.value)}
                placeholder="123456"
              />
            </Field>
          </div>
        </Modal>
      )}
    </div>
  );
}

