// Plain-language wording for everyday users, with an optional "Technical view"
// (MQTT, WebSocket, latency stages ...) for demos and the viva.

export const ui = { tech: false };

/** Pick the simple or the technical wording. */
export const T = (simple, tech) => (ui.tech ? tech : simple);

const listeners = new Set();
export const onViewChange = (fn) => listeners.add(fn);

export function setTech(on) {
  ui.tech = !!on;
  document.body.classList.toggle("tech", ui.tech);
  try { localStorage.setItem("techView", ui.tech ? "1" : "0"); } catch { /* storage unavailable */ }
  listeners.forEach((fn) => fn(ui.tech));
}

export function initTechToggle() {
  let saved = false;
  try { saved = localStorage.getItem("techView") === "1"; } catch { /* ignore */ }
  const box = document.getElementById("techToggle");
  box.checked = saved;
  ui.tech = saved;
  document.body.classList.toggle("tech", saved);
  box.addEventListener("change", () => setTech(box.checked));
}

// ------------------------------------------------------------------ wording
export const EVENT_TEXT = {
  FIRE_DETECTED:   { icon: "🔥", simple: "Fire detected",                        tech: "FIRE DETECTED" },
  SMOKE_DETECTED:  { icon: "💨", simple: "Smoke detected",                       tech: "SMOKE DETECTED" },
  INTRUSION:       { icon: "⚠", simple: "Someone entered a restricted area",    tech: "INTRUSION DETECTED" },
  LOITERING:       { icon: "⏱", simple: "Someone is staying in a restricted area", tech: "LOITERING DETECTED" },
  CROWD_ALERT:     { icon: "👥", simple: "Crowd gathering",                      tech: "CROWD ALERT" },
  PERSON_DETECTED: { icon: "🧍", simple: "Person spotted",                       tech: "PERSON DETECTED" },
};
export const eventTitle = (type) => {
  const e = EVENT_TEXT[type];
  return e ? T(e.simple, e.tech) : type;
};
export const eventIcon = (type) => (EVENT_TEXT[type] || { icon: "•" }).icon;

export const PRIORITY = { critical: "Urgent", high: "High", medium: "Medium", low: "Low" };
export const priorityText = (s) => T(PRIORITY[s] || s, s);

export const STATUS_TEXT = { ACTIVE: "Happening now", RESOLVED: "Cleared", LOGGED: "Recorded" };
export const statusText = (s) => T(STATUS_TEXT[s] || s, s);

export const SOURCE_TEXT = { webcam: "Laptop camera", rtsp: "Phone camera", http: "Phone camera",
  file: "Video file" };

export function cameraStatusText(s) {
  return T({ ONLINE: "Connected", CONNECTING: "Connecting…", OFFLINE: "Not connected",
    "CONNECTION LOST": "Connection lost", DISABLED: "Turned off" }[s] || s, s);
}

/** A friendly explanation of why a camera is not showing video. */
export function cameraHelp(info) {
  if (ui.tech) return info.error || "Retrying automatically…";
  if (info.status === "DISABLED") return "This camera is turned off. You can turn it on in Settings.";
  if (info.status === "CONNECTING") return "Trying to connect…";
  if (info.type === "webcam") {
    return "The laptop camera isn't available. Close other apps using it (Zoom, FaceTime) and "
      + "allow camera access for Terminal in System Settings → Privacy & Security → Camera.";
  }
  if (info.type === "file") return "The demo video file could not be found.";
  return "Can't reach the phone. Check that the phone is on the same Wi-Fi as this laptop and the "
    + "camera app is running. Trying again automatically…";
}

export const certainty = (c) => (c == null ? "—" : T(`${Math.round(c * 100)}% sure`, `${Math.round(c * 100)}%`));

export function clockTime(iso) {
  if (!iso) return "—";
  if (ui.tech) return iso.slice(11, 19);
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso.slice(11, 19)
    : d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" });
}

export function dateTime(iso) {
  if (!iso) return "—";
  if (ui.tech) return `${iso.slice(0, 10)} ${iso.slice(11, 19)}`;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso
    : d.toLocaleString([], { day: "numeric", month: "short", hour: "numeric", minute: "2-digit", second: "2-digit" });
}
