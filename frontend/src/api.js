const BASE = "";

let token = localStorage.getItem("pharmaos_token") || "";
let me = JSON.parse(localStorage.getItem("pharmaos_me") || "null");

export function setAuth(t, user) {
  token = t || "";
  me = user || null;
  if (t) localStorage.setItem("pharmaos_token", t);
  else localStorage.removeItem("pharmaos_token");
  if (user) localStorage.setItem("pharmaos_me", JSON.stringify(user));
  else localStorage.removeItem("pharmaos_me");
}

export function getToken() { return token; }
export function getMe() { return me; }

const listeners = new Set();
export function onAuthChange(fn) { listeners.add(fn); return () => listeners.delete(fn); }
function emit() { listeners.forEach((fn) => fn(me)); }

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
  if (res.status === 401) {
    setAuth(null, null); emit();
    throw new Error((data && data.message) || "Session expired");
  }
  if (!res.ok) {
    const msg = (data && (data.message || data.error)) || `Request failed (${res.status})`;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

export async function login(email, password) {
  const data = await api("/api/auth/login", { method: "POST", body: { email, password } });
  setAuth(data.access_token, data.user || { email, roles: [] });
  emit();
  return data;
}

export function logout() { setAuth(null, null); emit(); }
