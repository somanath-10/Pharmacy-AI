import React, { createContext, useContext, useState, useCallback } from "react";

/* ---------- toasts ---------- */
const ToastCtx = createContext(() => {});
export function ToastHost({ children }) {
  const [toasts, setToasts] = useState([]);
  const push = useCallback((msg, kind = "ok") => {
    const id = Math.random().toString(36).slice(2);
    setToasts((t) => [...t, { id, msg, kind }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 3500);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="toast-host">
        {toasts.map((t) => (
          <div key={t.id} className={`toast ${t.kind}`}>{t.msg}</div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}
export const useToast = () => useContext(ToastCtx);

/* ---------- primitives ---------- */
export function PageHead({ title, sub, children }) {
  return (
    <div className="topbar">
      <div>
        <h1>{title}</h1>
        {sub && <div className="sub">{sub}</div>}
      </div>
      <div className="spacer" />
      {children}
    </div>
  );
}

export function Kpi({ ico, tone = "blue", label, value, delta, foot, pulse }) {
  return (
    <div className="card kpi">
      <div className={`kpi-ico ${tone}`}>{ico}</div>
      <div>
        <div className="lbl">{label}</div>
        <b>{value}</b>{" "}
        {delta && <span className={`delta ${String(delta).startsWith("+") ? "" : "warn"}`}>{delta}</span>}
        {foot && <div className="foot">{foot}</div>}
      </div>
    </div>
  );
}

const STATUS_TONES = {
  APPROVED: "green", RELEASED: "green", PAID: "green", COMPLETED: "green",
  MATCHED: "green", ACTIVE: "green", SENT: "blue", PLANNED: "blue",
  DRAFT: "gray", CLOSED: "gray", CANCELLED: "gray", DISPOSED: "gray",
  PENDING_APPROVAL: "orange", PENDING: "orange", PROPOSED: "orange",
  QC_PENDING: "orange", IN_TRANSIT: "blue", DISPATCHED: "blue",
  RECEIVED: "blue", EXTRACTED: "blue", IN_MATCHING: "blue",
  MATCH_FAILED: "red", REJECTED: "red", ON_HOLD: "red", QUARANTINE: "orange",
  FG_QUARANTINE: "orange", DISPENSING: "purple", IN_PROCESS: "purple",
  PACKAGING: "purple", TESTING: "purple", REVIEW: "purple",
  APPROVE: "green", REJECT: "red",
};
export function Badge({ value }) {
  const tone = STATUS_TONES[String(value || "").toUpperCase()] || "gray";
  return <span className={`badge ${tone}`}>{String(value || "—").replace(/_/g, " ")}</span>;
}

export function Table({ columns, rows, onRow, empty = "Nothing here yet" }) {
  if (!rows || rows.length === 0) return <div className="empty">{empty}</div>;
  return (
    <table className="table">
      <thead>
        <tr>{columns.map((c) => <th key={c.key}>{c.label}</th>)}</tr>
      </thead>
      <tbody>
        {rows.map((r, i) => (
          <tr key={r._key || r.id || i}
              className={onRow ? "clickable" : ""}
              onClick={onRow ? () => onRow(r) : undefined}>
            {columns.map((c) => (
              <td key={c.key} className={c.mono ? "mono" : ""}>
                {c.render ? c.render(r) : (r[c.key] ?? "—")}
              </td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function Money({ value, currency = "₹" }) {
  const n = Number(value || 0);
  return <span className="mono">{currency}{n.toLocaleString("en-IN", { maximumFractionDigits: 2 })}</span>;
}

export function When(iso) {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return d.toLocaleString("en-IN", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
  } catch { return iso; }
}

export function Empty({ children }) { return <div className="empty">{children}</div>; }
