// Dashboard entry point: status bar, tabs, Disaster Mode, WebSocket wiring.
import { api, esc, toast } from "./api.js";
import { T, initTechToggle, onViewChange, cameraStatusText } from "./ui.js";
import { connect, onMessage, socketState } from "./socket.js";
import { loadCameras, upsertCamera, loadRecentAlerts, handleEvent, setDisasterView, setSound,
  camerasSnapshot, rerenderCameras, rerenderAlerts } from "./live.js";
import { initEvents, loadEvents } from "./events.js";
import { refreshMetrics } from "./metrics.js";
import { renderCameraForms, initZoneEditor, selectZoneCamera } from "./config.js";

let activeTab = "live";

// ------------------------------------------------------------------ status
const LABELS = {
  backend:   ["Server", "Backend"],
  websocket: ["Live updates", "WebSocket"],
  mqtt:      ["Alert delivery", "MQTT"],
  database:  ["Alert history", "Database"],
  ai:        ["Person detection", "AI Engine"],
  fire:      ["Fire detection", "Fire Model"],
};
const problems = new Map();   // key -> short description, for the summary pill

function pill(key, state, simpleText, techText, title = "") {
  const el = document.querySelector(`.pill[data-key="${key}"]`);
  if (!el) return;
  el.className = `pill ${state}`;
  el.querySelector("span").textContent = T(...LABELS[key]);
  el.querySelector("b").textContent = T(simpleText, techText);
  el.title = title;
  if (state === "bad") problems.set(key, LABELS[key][0]); else problems.delete(key);
}

let lastStatus = null;

async function refreshStatus() {
  let s;
  try {
    s = await api("/api/system/status");
  } catch {
    pill("backend", "bad", "Not reachable", "OFFLINE", "The dashboard cannot reach the server. Is run.py still running?");
    ["mqtt", "database", "ai", "fire"].forEach((k) => pill(k, "", "—", "—"));
    renderSummary(true);
    return;
  }
  lastStatus = s;
  renderStatus(s);
}

function renderStatus(s) {
  pill("backend", "ok", "Working", "ONLINE");
  const mqttOk = s.mqtt.status === "online";
  pill("mqtt", mqttOk ? "ok" : "bad", mqttOk ? "Working" : "Not working", mqttOk ? "CONNECTED" : "OFFLINE",
    T(mqttOk ? "Alerts are being delivered normally."
      : "The alert service (Mosquitto) isn't running. Alerts still appear here, but start Mosquitto to fix this.",
    `Broker ${s.mqtt.broker}${s.mqtt.error ? " — " + s.mqtt.error : ""}`));
  const dbOk = s.database.status === "online";
  pill("database", dbOk ? "ok" : "bad", dbOk ? "Saving" : "Not saving", dbOk ? "CONNECTED" : "ERROR",
    T("Alerts and photos are saved on this laptop.", `SQLite: ${s.database.path}`));
  const ai = s.ai_engine.status;
  pill("ai", ai === "running" ? "ok" : ai === "loading" ? "warn" : "bad",
    { running: "On", loading: "Starting…", error: "Not working", disabled: "Off" }[ai] || ai,
    { running: "RUNNING", loading: "LOADING", error: "NO MODEL", disabled: "DISABLED" }[ai] || ai.toUpperCase(),
    T(ai === "error" ? "The AI model file is missing. See the setup steps in the README." : "Finds people and vehicles in the video.",
      s.ai_engine.detector));
  const fm = s.fire_model.status;
  pill("fire", fm === "loaded" ? "ok" : fm === "error" ? "bad" : "warn",
    fm === "loaded" ? "On" : fm === "error" ? "Not working" : "Not set up",
    fm === "loaded" ? "LOADED" : fm === "error" ? "ERROR" : "NOT CONFIGURED",
    T(fm === "loaded" ? "Watching for fire and smoke."
      : "Add a fire/smoke model file to the models folder to turn this on.", s.fire_model.message));

  document.getElementById("cameraPills").innerHTML = s.cameras.map((c) => {
    const st = c.status === "ONLINE" ? "ok" : c.status === "DISABLED" ? "" : "bad";
    const name = T(c.name, c.id.replace("camera_", "Camera "));
    const value = T(cameraStatusText(c.status), c.status);
    const tip = T(c.status === "ONLINE" ? "Connected" : cameraStatusText(c.status), c.error || c.name);
    return `<div class="pill ${st}" title="${esc(tip)}"><i></i><span>${esc(name)}</span><b>${esc(value)}</b></div>`;
  }).join("");
  s.cameras.forEach((c) => {
    if (c.status !== "ONLINE" && c.status !== "DISABLED") problems.set(`cam_${c.id}`, c.name);
    else problems.delete(`cam_${c.id}`);
  });
  renderSummary(false);

  const toggle = document.getElementById("disasterToggle");
  if (toggle.checked !== s.disaster_mode) {
    toggle.checked = s.disaster_mode;
    setDisasterView(s.disaster_mode);
  }
}

function renderSummary(serverDown) {
  const el = document.getElementById("summaryPill");
  const n = problems.size;
  el.className = `pill summary ${serverDown || n ? "bad" : "ok"}`;
  el.querySelector("b").textContent = serverDown ? "Server not reachable"
    : n === 0 ? "Everything is working" : `${n} thing${n > 1 ? "s" : ""} need${n > 1 ? "" : "s"} attention`;
  el.title = n ? `Needs attention: ${[...problems.values()].join(", ")}` : "";
}

function updateSocketPill() {
  pill("websocket", socketState.connected ? "ok" : "bad",
    socketState.connected ? "Connected" : "Reconnecting…", socketState.connected ? "CONNECTED" : "RECONNECTING",
    T("New alerts appear instantly without refreshing the page.",
      socketState.rtt != null ? `Round-trip ${socketState.rtt} ms` : ""));
  renderSummary(false);
}

// -------------------------------------------------------------------- tabs
function showTab(name) {
  activeTab = name;
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  document.querySelectorAll(".view").forEach((v) => v.classList.toggle("active", v.id === `view-${name}`));
  if (name === "events") loadEvents();
  if (name === "metrics") refreshMetrics().catch(() => {});
  if (name === "config") {
    renderCameraForms(camerasSnapshot(), upsertCamera);
    selectZoneCamera(document.getElementById("zoneCamera").value);
  }
}

// -------------------------------------------------------------------- init
async function init() {
  setInterval(() => {
    document.getElementById("clock").textContent = new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  }, 1000);

  document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => showTab(t.dataset.tab)));

  initTechToggle();
  onViewChange(() => {       // switch between plain and technical wording
    if (lastStatus) renderStatus(lastStatus);
    updateSocketPill();
    rerenderCameras();
    rerenderAlerts();
    if (activeTab === "events") loadEvents();
    if (activeTab === "metrics") refreshMetrics().catch(() => {});
    if (activeTab === "config") renderCameraForms(camerasSnapshot(), upsertCamera);
  });

  document.getElementById("disasterToggle").addEventListener("change", async (e) => {
    const enabled = e.target.checked;
    try {
      await api("/api/system/disaster-mode", { method: "POST", body: { enabled } });
      setDisasterView(enabled);
      toast(enabled ? "<b>Disaster Mode is on</b><br>Focusing on finding people, fire, smoke and crowds"
        : "<b>Disaster Mode is off</b><br>Back to normal security monitoring", enabled ? "high" : "low");
    } catch (err) {
      e.target.checked = !enabled;
      toast(`<b>Could not change the mode.</b> ${esc(err.message)}`, "critical");
    }
  });

  const soundBtn = document.getElementById("soundToggle");
  let sound = true;
  soundBtn.addEventListener("click", () => {
    sound = !sound; setSound(sound); soundBtn.classList.toggle("off", !sound);
  });

  onMessage((msg) => {
    switch (msg.type) {
      case "_socket": updateSocketPill(); break;
      case "event":
        handleEvent(msg);
        if (activeTab === "events" && msg.action === "created") loadEvents();
        break;
      case "camera_status": { const { type, ws_ts, ...cam } = msg; upsertCamera(cam); break; }
      case "latency": if (activeTab === "metrics") refreshMetrics().catch(() => {}); break;
      default: break;
    }
  });

  initZoneEditor();
  await refreshStatus();
  let cameras = [];
  try { cameras = await loadCameras(); } catch { toast("Could not load the cameras. Is the server running?", "critical"); }
  initEvents(cameras);
  try { await loadRecentAlerts(); } catch { /* backend not ready yet */ }
  connect();

  setInterval(refreshStatus, 3000);
  setInterval(() => { updateSocketPill(); if (activeTab === "metrics") refreshMetrics().catch(() => {}); }, 3000);
  setInterval(() => loadCameras().catch(() => {}), 10000);  // full camera info (protocol, errors)
}

init();
