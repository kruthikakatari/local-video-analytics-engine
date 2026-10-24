// Small shared helpers: REST calls, formatting, event metadata.

export async function api(path, options = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
    body: options.body ? JSON.stringify(options.body) : undefined,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch { /* not JSON */ }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.json();
}

export const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => (
  { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

export const EVENT_META = {
  FIRE_DETECTED:   { icon: "🔥", title: "FIRE DETECTED" },
  SMOKE_DETECTED:  { icon: "💨", title: "SMOKE DETECTED" },
  INTRUSION:       { icon: "⚠", title: "INTRUSION DETECTED" },
  LOITERING:       { icon: "⏱", title: "LOITERING DETECTED" },
  CROWD_ALERT:     { icon: "👥", title: "CROWD ALERT" },
  PERSON_DETECTED: { icon: "🧍", title: "PERSON DETECTED" },
};
export const meta = (t) => EVENT_META[t] || { icon: "•", title: t };

export const pct = (c) => (c == null ? "—" : `${Math.round(c * 100)}%`);
export const timeOf = (iso) => (iso ? iso.slice(11, 19) : "—");
export const dateTimeOf = (iso) => (iso ? `${iso.slice(0, 10)} ${iso.slice(11, 19)}` : "—");
export const snapshotUrl = (p) => (p ? `/${p}` : null);

export function fmtNum(v, digits = 0) {
  if (v == null || Number.isNaN(v)) return "—";
  return Number(v).toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits });
}

export function fmtBytes(b) {
  if (b == null) return "—";
  if (b < 1024) return `${fmtNum(b)} B`;
  if (b < 1024 * 1024) return `${fmtNum(b / 1024, 1)} KB`;
  return `${fmtNum(b / 1024 / 1024, 2)} MB`;
}

export function cameraLabel(id, name) {
  const num = (id || "").replace(/^camera_/i, "");
  return `CAMERA ${esc(num)} — ${esc((name || "").toUpperCase())}`;
}

export function toast(html, severity = "low", ms = 5000) {
  const el = document.createElement("div");
  el.className = `toast sev-${severity}`;
  el.innerHTML = html;
  document.getElementById("toasts").prepend(el);
  setTimeout(() => el.remove(), ms);
}

// Hover tooltip shared by charts
const tip = () => document.getElementById("tooltip");
export function bindTooltip(el, html) {
  el.addEventListener("mousemove", (e) => {
    const t = tip();
    t.innerHTML = html;
    t.hidden = false;
    t.style.left = `${Math.min(e.clientX + 14, window.innerWidth - 290)}px`;
    t.style.top = `${e.clientY + 14}px`;
  });
  el.addEventListener("mouseleave", () => { tip().hidden = true; });
}
