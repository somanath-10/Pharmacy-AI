const BASE = "";

let token = localStorage.getItem("pharmaos_token") || "";
let refresh = localStorage.getItem("pharmaos_refresh") || "";
let me = JSON.parse(localStorage.getItem("pharmaos_me") || "null");

export function setAuth(t, user, r = undefined) {
  token = t || "";
  if (r !== undefined) refresh = r || "";
  if (t) localStorage.setItem("pharmaos_token", t);
  else localStorage.removeItem("pharmaos_token");
  if (r !== undefined) {
    if (r) localStorage.setItem("pharmaos_refresh", r);
    else localStorage.removeItem("pharmaos_refresh");
  }
  if (user) localStorage.setItem("pharmaos_me", JSON.stringify(user));
  else localStorage.removeItem("pharmaos_me");
}

export function getToken() { return token; }
export function getRefresh() { return refresh; }
export function getMe() { return me; }

const listeners = new Set();
export function onAuthChange(fn) { listeners.add(fn); return () => listeners.delete(fn); }
function emit() { listeners.forEach((fn) => fn(me)); }

let refreshing = null;

async function doRefresh() {
  if (!refresh) return false;
  if (!refreshing) {
    refreshing = (async () => {
      try {
        const res = await fetch(BASE + "/api/auth/refresh", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ refresh_token: refresh }),
        });
        if (!res.ok) return false;
        const data = await res.json();
        setAuth(data.access_token, me, data.refresh_token);
        return true;
      } catch {
        return false;
      } finally {
        refreshing = null;
      }
    })();
  }
  return refreshing;
}

function extractErrorMessage(data, status, path) {
  if (data) {
    if (typeof data.detail === "string") return data.detail;
    if (Array.isArray(data.detail) && data.detail.length > 0) {
      return data.detail.map((d) => d.msg || JSON.stringify(d)).join(", ");
    }
    if (data.message) return typeof data.message === "string" ? data.message : JSON.stringify(data.message);
    if (data.error) return typeof data.error === "string" ? data.error : JSON.stringify(data.error);
  }
  if (status === 401) {
    return path.startsWith("/api/auth/") ? "Invalid email or password" : "Session expired. Please sign in again.";
  }
  return `Request failed (${status})`;
}

export async function api(path, { method = "GET", body, headers = {}, idemKey } = {}) {
  const h = { ...headers };
  if (token) h.Authorization = `Bearer ${token}`;
  if (body !== undefined) h["Content-Type"] = "application/json";
  if (idemKey) h["Idempotency-Key"] = idemKey;
  const res = await fetch(BASE + path, {
    method, headers: h,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  let data = null;
  try { data = await res.json(); } catch { /* empty */ }
  if (res.status === 401 && refresh && !path.startsWith("/api/auth/")) {
    // short-lived access token expired → rotate refresh once, retry request
    const ok = await doRefresh();
    if (ok) return api(path, { method, body, headers, idemKey });
    setAuth(null, null); emit();
    throw new Error(extractErrorMessage(data, 401, path));
  }
  if (res.status === 401) {
    if (!path.startsWith("/api/auth/")) { setAuth(null, null); emit(); }
    throw new Error(extractErrorMessage(data, 401, path));
  }
  if (!res.ok) {
    throw new Error(extractErrorMessage(data, res.status, path));
  }
  return data;
}

export async function login(email, password, mfaCode) {
  const data = await api("/api/auth/login", {
    method: "POST",
    body: { email, password, ...(mfaCode ? { mfa_code: mfaCode } : {}) },
  });
  setAuth(data.access_token, data.user || { email, roles: [] }, data.refresh_token);
  emit();
  return data;
}

export async function logout() {
  // server-side revocation of the refresh token, then clear local session
  try {
    if (refresh) {
      await fetch(BASE + "/api/auth/logout", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
        body: JSON.stringify({ refresh_token: refresh }),
      });
    }
  } catch { /* best effort */ }
  setAuth(null, null, "");
  emit();
}
