import React, { useEffect, useState } from "react";

/* ── status → tone mapping ──────────────────────────── */
const STATUS_TONES = {
  AVAILABLE: "green", QA_RELEASED: "green", COMPLETED: "green", APPROVED: "green",
  RELEASED: "green", PASSED: "green", CLOSED: "green", DELIVERED: "green", POD: "green",
  PAID: "green", ACTIVE: "green", EFFECTIVE: "green", SATISFACTORY: "green", PUBLISHED: "green",
  RESOLVED: "green", VERIFIED: "green", QUALIFIED: "green", ACCEPTED: "green", WON: "green",
  AWARDED: "green", AUTO_EXECUTED: "green", DONE: "green", CLEAR: "green", CONVERTED: "green",
  RECEIVED: "blue", OPEN: "blue", PENDING: "blue", PLANNED: "blue", SENT: "blue",
  SUBMITTED: "blue", IN_PROGRESS: "blue", IN_TRANSIT: "blue", DISPATCHED: "blue",
  ACKNOWLEDGED: "blue", ACK: "blue", QC_SAMPLING: "blue", QC_TESTING: "blue",
  PICKED: "blue", PACKED: "blue", STAGED: "blue", SCHEDULED: "blue", ASSIGNED: "blue",
  WAVE: "blue", RUNNING: "blue", TESTING: "blue", UNDER_REVIEW: "blue", NEW: "blue",
  INVESTIGATION: "blue", EXECUTION: "blue", VERIFICATION: "blue",
  QUARANTINE: "orange", QUALITY_HOLD: "orange", HOLD: "orange", PENDING_QC: "orange",
  PENDING_APPROVAL: "orange", PARTIAL: "orange", ANOMALY: "orange", WARNING: "orange",
  OOS: "orange", OOT: "orange", ESCALATED: "orange", RETURN_QUARANTINE: "orange",
  ON_HOLD: "orange", QUEUED: "orange", RESPONSE: "orange",
  RECALLED: "red", REJECTED: "red", FAILED: "red", EXPIRED: "red", DAMAGED: "red",
  CANCELLED: "red", OVERDUE: "red", FAILURE: "red", SHORT: "red", LOST: "red",
  BLOCKED: "red", REWORK: "red", SCRAP: "red",
  RESERVED: "purple", RTV_PENDING: "purple", RTV: "purple",
  DRAFT: "gray", EXPECTED: "gray", DISPOSED: "gray", PENDING_GRN: "gray", NONE: "gray",
  // coverage for every backend state-machine state (gray default otherwise)
  MATCHED: "green", QC_PASSED: "green", PUTAWAY_DONE: "green", RESTOCKED: "green",
  RECONCILED: "green", RMA_APPROVED: "green", AUTHORIZED: "green", DISPENSED: "green",
  ISSUED: "green", BATCH_RELEASED: "green", VALIDATED: "green",
  EXTRACTED: "blue", POSTED: "blue", LIVE: "blue", SAMPLING: "blue",
  RESULTS_ENTERED: "blue", REVIEW: "blue", IN_REVIEW: "blue", IMPLEMENTATION: "blue",
  IMPLEMENTED: "blue", IDENTIFIED: "blue", NOTIFIED: "blue", REQUESTED: "blue",
  PICKUP_SCHEDULED: "blue", DISPOSITIONED: "blue", INSPECTED: "blue", ALLOCATED: "blue",
  LOADING: "blue", EVALUATION: "blue", RECOMMENDATION: "blue", CONTRACTED: "blue",
  PARTIALLY_PAID: "blue", PARTIALLY_RECEIVED: "blue", CLARIFIED: "blue",
  CLARIFICATION: "blue", DUPLICATE_CHECK: "blue", FOLLOW_UP: "blue",
  MEDICAL_REVIEW: "blue", TRIAGE: "blue", QA_REVIEW: "blue", QC_COMPLETE: "blue",
  QA_QUALIFICATION: "blue", COMMERCIAL_REVIEW: "blue", DOCS_PENDING: "blue",
  PROPOSED: "blue", PENDING_RX: "blue", QC_PENDING: "blue", CREDIT_NOTE_ISSUED: "blue",
  RX_APPROVED: "green",
  HELD: "orange", OOS_INVESTIGATION: "orange", QC_PARTIAL: "orange",
  FG_QUARANTINE: "orange", CONTAINED: "orange", SUSPENDED: "orange",
  DISPUTED: "orange", WRITTEN_OFF: "orange", AWAITING_APPROVAL: "orange",
  QC_FAILED: "red", MATCH_FAILED: "red", RX_REJECTED: "red", BLACKLISTED: "red",
  RETIRED: "gray", DESTROYED: "gray", DISPOSAL: "gray",
};

export function Badge({ tone, value, children }) {
  const label = children ?? value ?? tone ?? "";
  const t = STATUS_TONES[String(tone ?? value ?? children ?? "").toUpperCase()] || "gray";
  return <span className={`badge ${t}`}>{String(label)}</span>;
}

/* ── table: supports both <Table head children> and <Table columns rows> ── */
export function Table({ head, children, columns, rows, empty, onRow }) {
  // declarative API: columns + rows
  if (columns) {
    const data = rows || [];
    if (data.length === 0)
      return <Empty art="🎉" title={empty || "Nothing here yet"} note="Records will appear as work flows through the system." />;
    return (
      <div className="table-wrap">
        <table className="table">
          <thead><tr>{columns.map((c) => <th key={c.key}>{c.label || c.key}</th>)}</tr></thead>
          <tbody>
            {data.map((r, i) => (
              <tr key={r._id || r.approval_id || r.id || i}
                  className={onRow ? "clickable" : ""}
                  onClick={onRow ? () => onRow(r) : undefined}>
                {columns.map((c) => <td key={c.key}>{c.render ? c.render(r) : String(r[c.key] ?? "")}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }
  // children API
  const kids = React.Children.toArray(children).filter(Boolean);
  if (kids.length === 0)
    return <Empty art="📭" title={empty || "Nothing here yet"} note="Records will appear as work flows through the system." />;
  return (
    <div className="table-wrap">
      <table className="table">
        <thead><tr>{head.map((h) => <th key={h}>{h}</th>)}</tr></thead>
        <tbody>{kids}</tbody>
      </table>
    </div>
  );
}

/* ── KPI card ───────────────────────────────────────── */
export function Kpi({ ico, label, value, delta, foot, tone = "blue" }) {
  return (
    <div className="card kpi hoverable rise">
      <div className={`kpi-ico ${tone}`}>{ico}</div>
      <div style={{ minWidth: 0 }}>
        <div className="lbl">{label}</div>
        <b>{value ?? "—"}</b>
        {delta && <span className={`delta ${String(delta).startsWith("!") ? "warn" : ""}`}>{delta}</span>}
        {foot && <div className="foot">{foot}</div>}
      </div>
    </div>
  );
}

/* ── When: date formatter (When(ts) → readable date) ── */
export function When(v) {
  if (!v) return "—";
  try {
    const d = new Date(v);
    if (isNaN(d)) return String(v);
    return d.toLocaleString(undefined, { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
  } catch { return String(v); }
}

/* ── money formatter ────────────────────────────────── */
export function Money({ v, value, cur = "₹", compact = false }) {
  const n = Number(v ?? value);
  if (v == null && value == null) return "—";
  if (isNaN(n)) return "—";
  if (compact && Math.abs(n) >= 100000) {
    if (Math.abs(n) >= 10000000) return `₹${(n / 10000000).toFixed(1)}Cr`;
    return `₹${(n / 100000).toFixed(1)}L`;
  }
  return `${cur}${n.toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
}

/* ── modal ──────────────────────────────────────────── */
export function Modal({ title, sub, onClose, children, footer, wide, open = true }) {
  if (!open) return null;
  return <ModalInner key={title} title={title} sub={sub} onClose={onClose} footer={footer} wide={wide}>{children}</ModalInner>;
}

function ModalInner({ title, sub, onClose, children, footer, wide }) {
  useEffect(() => {
    const h = (e) => { if (e.key === "Escape") onClose?.(); };
    window.addEventListener("keydown", h);
    document.body.style.overflow = "hidden";
    return () => { window.removeEventListener("keydown", h); document.body.style.overflow = ""; };
  }, [onClose]);
  return (
    <div className="overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose?.(); }}>
      <div className={`modal${wide ? " wide" : ""}`} onMouseDown={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <div style={{ flex: 1 }}>
            <h3>{title}</h3>
            {sub && <div className="sub">{sub}</div>}
          </div>
          <button className="icon-btn" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  );
}

/* ── confirm dialog (replaces window.confirm) ───────── */
export function Confirm({ title = "Are you sure?", message, danger, okLabel = "Confirm", cancelLabel = "Cancel", onOk, onCancel }) {
  const [busy, setBusy] = useState(false);
  return (
    <Modal
      title={title} onClose={onCancel} sub={message}
      footer={
        <>
          <button className="btn ghost" onClick={onCancel} disabled={busy}>{cancelLabel}</button>
          <div style={{ flex: 1 }} />
          <button
            className={`btn ${danger ? "danger" : "primary"}`} disabled={busy}
            onClick={async () => { setBusy(true); try { await onOk?.(); } finally { setBusy(false); } }}
          >
            {busy && <span className="spin" />}{okLabel}
          </button>
        </>
      }
    >
      {danger && (
        <div className="error-box" style={{ marginBottom: 0 }}>
          <span>⚠️</span>
          <span>This action may be irreversible. Please review carefully before confirming.</span>
        </div>
      )}
    </Modal>
  );
}

/* ── tabs ───────────────────────────────────────────── */
export function Tabs({ tabs, active, onChange }) {
  return (
    <div className="tabs">
      {tabs.map((t) => (
        <button key={t} className={`tab${active === t ? " on" : ""}`} onClick={() => onChange(t)}>
          {t}
        </button>
      ))}
    </div>
  );
}

export function CountTabs({ tabs, active, onChange }) {
  return (
    <div className="tabs">
      {tabs.map(({ label, count }) => (
        <button key={label} className={`tab${active === label ? " on" : ""}`} onClick={() => onChange(label)}>
          {label}
          {typeof count === "number" && <span className="cnt">{count}</span>}
        </button>
      ))}
    </div>
  );
}

/* ── right-side drawer ──────────────────────────────── */
export function Drawer({ title, sub, onClose, children, footer, open = true }) {
  if (!open) return null;
  return <DrawerInner title={title} sub={sub} onClose={onClose} footer={footer}>{children}</DrawerInner>;
}

function DrawerInner({ title, sub, onClose, children, footer }) {
  useEffect(() => {
    const h = (e) => { if (e.key === "Escape") onClose?.(); };
    window.addEventListener("keydown", h);
    document.body.style.overflow = "hidden";
    return () => { window.removeEventListener("keydown", h); document.body.style.overflow = ""; };
  }, [onClose]);
  return (
    <>
      <div className="drawer-overlay" onMouseDown={onClose} />
      <aside className="drawer">
        <div className="drawer-head">
          <div style={{ flex: 1 }}>
            <h3 style={{ margin: 0, fontSize: 16, color: "var(--navy)", fontWeight: 800 }}>{title}</h3>
            {sub && <div className="sub muted" style={{ fontSize: 12, marginTop: 3 }}>{sub}</div>}
          </div>
          <button className="icon-btn" onClick={onClose} aria-label="Close">✕</button>
        </div>
        <div className="drawer-body">{children}</div>
        {footer && <div className="modal-foot" style={{ borderTop: "1px solid var(--line-2)" }}>{footer}</div>}
      </aside>
    </>
  );
}

/* ── field wrapper ──────────────────────────────────── */
export function Field({ label, hint, children }) {
  return (
    <div className="field">
      {label && <label>{label}</label>}
      {children}
      {hint && <div className="hint">{hint}</div>}
    </div>
  );
}

export function Select({ value, onChange, options, placeholder }) {
  return (
    <select value={value} onChange={(e) => onChange(e.target.value)}>
      {placeholder !== undefined && <option value="">{placeholder}</option>}
      {options.map((o) => {
        const v = typeof o === "string" ? o : o.value;
        const l = typeof o === "string" ? o : o.label;
        return <option key={v} value={v}>{l}</option>;
      })}
    </select>
  );
}

/* ── workflow stepper ───────────────────────────────── */
export function Stepper({ steps, current }) {
  const idx = steps.indexOf(current);
  return (
    <div className="wf-flow">
      {steps.map((s, i) => (
        <React.Fragment key={s}>
          {i > 0 && <span className="wf-arrow">→</span>}
          <div className={`wf-node ${i < idx ? "done" : i === idx ? "active" : "pending"}`}>
            <b>{s}</b>
            <small>{i < idx ? "completed" : i === idx ? "current stage" : "upcoming"}</small>
          </div>
        </React.Fragment>
      ))}
    </div>
  );
}

/* ── vertical timeline (audit / history) ────────────── */
export function Timeline({ items }) {
  if (!items || items.length === 0)
    return <div className="muted small" style={{ padding: "8px 0" }}>No history recorded.</div>;
  return (
    <div className="timeline">
      {items.map((it, i) => (
        <div key={i} className={`tl-item ${it.tone || ""}`}>
          <b>{it.title}</b>
          <div className="small muted" style={{ marginTop: 2 }}>{it.detail}</div>
          {it.meta && <div className="tiny muted" style={{ marginTop: 3 }}>{it.meta}</div>}
        </div>
      ))}
    </div>
  );
}

/* ── loading skeleton ───────────────────────────────── */
export function Skeleton({ rows = 5 }) {
  return (
    <div>
      {Array.from({ length: rows }).map((_, i) => (
        <div className="skel-row" key={i} style={{ animationDelay: `${i * 60}ms` }}>
          <div className="skeleton s-circle" />
          <div className="skeleton s-line" />
          <div className="skeleton s-line short" />
        </div>
      ))}
    </div>
  );
}

export function Spinner({ label = "Loading…" }) {
  return (
    <div className="empty">
      <div className="spinner" />
      <div style={{ marginTop: 10 }}>{label}</div>
    </div>
  );
}

/* ── empty state ────────────────────────────────────── */
export function Empty({ art = "🗂️", title = "No data", note }) {
  return (
    <div className="empty">
      <span className="art">{art}</span>
      <b>{title}</b>
      {note && <div className="tiny">{note}</div>}
    </div>
  );
}

export function ErrorBox({ children }) {
  if (!children) return null;
  return <div className="error-box"><span>⚠️</span><span>{children}</span></div>;
}

export function OkBox({ children }) {
  if (!children) return null;
  return <div className="ok-box"><span>✅</span><span>{children}</span></div>;
}

/* ── progress bar ───────────────────────────────────── */
export function Progress({ value, tone }) {
  const pct = Math.max(0, Math.min(100, Number(value) || 0));
  return (
    <div className={`progress ${tone || ""}`}>
      <i style={{ width: `${pct}%` }} />
    </div>
  );
}

/* ── If (conditional render) ────────────────────────── */
export function If({ cond, children }) {
  return cond ? children : null;
}

/* ── global toast bus ───────────────────────────────── */
const toastSubs = new Set();
export function toast(msg, tone = "info") {
  const t = { id: Math.random().toString(36).slice(2), msg, tone };
  toastSubs.forEach((fn) => fn(t));
}
toast.ok = (m) => toast(m, "ok");
toast.err = (m) => toast(m, "err");

export function Toasts() {
  const [items, setItems] = useState([]);
  useEffect(() => {
    const fn = (t) => {
      setItems((list) => [...list, t]);
      setTimeout(() => setItems((list) => list.filter((x) => x.id !== t.id)), 4200);
    };
    toastSubs.add(fn);
    return () => { toastSubs.delete(fn); };
  }, []);
  return (
    <div className="toast-host">
      {items.map((t) => (
        <div key={t.id} className={`toast ${t.tone}`}>
          <span className="t-ico">{t.tone === "ok" ? "✅" : t.tone === "err" ? "⛔" : "ℹ️"}</span>
          {t.msg}
        </div>
      ))}
    </div>
  );
}

/* compat: legacy per-component hook + host */
export function ToastHost({ children }) {
  return (
    <>
      {children}
      <Toasts />
    </>
  );
}

/* ── toast system (per-component) ───────────────────── */
export function useToast() {
  const [toasts, setToasts] = useState([]);
  const push = (msg, tone = "info") => {
    const id = Math.random().toString(36).slice(2);
    setToasts((t) => [...t, { id, msg, tone }]);
    setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4200);
  };
  const host = (
    <div className="toast-host">
      {toasts.map((t) => (
        <div key={t.id} className={`toast ${t.tone}`}>
          <span className="t-ico">{t.tone === "ok" ? "✅" : t.tone === "err" ? "⛔" : "ℹ️"}</span>
          {t.msg}
        </div>
      ))}
    </div>
  );
  return { toast: push, toastHost: host };
}
