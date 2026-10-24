// Live View: camera cards (MJPEG video) + Alerts (WebSocket) +
// Disaster Mode situational-awareness panel.
import { api, esc, snapshotUrl, toast } from "./api.js";
import { openEvent } from "./events.js";
import { T, ui, eventTitle, eventIcon, priorityText, statusText, cameraStatusText, cameraHelp,
  certainty, clockTime, SOURCE_TEXT } from "./ui.js";

const cams = new Map();      // id -> {info, el}
const alerts = new Map();    // event id -> {event, el}
let located = 0;
let soundOn = true;
const MAX_ALERTS = 40;

export const setSound = (on) => { soundOn = on; };

// ------------------------------------------------------------------ cameras
export async function loadCameras() {
  const list = await api("/api/cameras");
  list.forEach(upsertCamera);
  return list;
}

export function camerasSnapshot() { return [...cams.values()].map((c) => c.info); }

const camNumber = (id) => (id || "").replace(/^camera_0*/i, "");

export function upsertCamera(info) {
  const grid = document.getElementById("cameraGrid");
  const id = info.id || info.camera_id;
  let c = cams.get(id);
  if (!c) {
    const el = document.createElement("article");
    el.className = "cam-card";
    el.innerHTML = `
      <div class="cam-head">
        <div class="cam-title"></div>
        <div class="badges"></div>
      </div>
      <div class="cam-video">
        <img alt="Live video from ${esc(id)}">
        <div class="cam-overlay" hidden><div class="ov-title"></div><small class="ov-detail"></small></div>
      </div>
      <div class="cam-stats"></div>`;
    const next = [...cams.keys()].sort().find((k) => k > id);   // keep cards in camera order
    grid.insertBefore(el, next ? cams.get(next).el : null);
    const img = el.querySelector("img");
    img.addEventListener("error", () => {   // stream interrupted (e.g. server restart): retry
      if (img.dataset.want === "1") setTimeout(() => { img.src = `/video/${id}?t=${Date.now()}`; }, 3000);
    });
    c = { info: {}, el };
    cams.set(id, c);
  }
  // Merge (live status messages carry only some fields)
  Object.assign(c.info, info, { id });
  renderCamera(c);
}

export function rerenderCameras() { cams.forEach(renderCamera); }

function renderCamera(c) {
  const i = c.info, el = c.el;
  const online = i.status === "ONLINE";
  el.querySelector(".cam-title").innerHTML = ui.tech
    ? `CAMERA ${esc(camNumber(i.id).padStart(2, "0"))} — ${esc((i.name || "").toUpperCase())}`
    : `${esc(i.name || i.id)}<div class="cam-sub">Camera ${esc(camNumber(i.id))}</div>`;

  const badges = [];
  badges.push(online ? `<span class="badge live">LIVE</span>`
    : `<span class="badge off">${esc(cameraStatusText(i.status || "—").toUpperCase())}</span>`);
  if (i.demo_mode) badges.push(`<span class="badge demo">DEMO MODE</span>`);
  const src = T(SOURCE_TEXT[i.type], i.protocol);
  if (src) badges.push(`<span class="badge">${esc(src)}</span>`);
  el.querySelector(".badges").innerHTML = badges.join("");
  el.classList.toggle("offline", !online);

  const stat = (label, value, cls = "") => `<div><span>${label}</span><b class="${cls}">${value}</b></div>`;
  const statusCls = online ? "st-online" : "st-offline";
  const stats = el.querySelector(".cam-stats");
  stats.classList.toggle("simple", !ui.tech);
  stats.innerHTML = ui.tech
    ? stat("Status", esc(i.status || "—"), `s-status ${statusCls}`)
      + stat("FPS", online ? Math.round(i.capture_fps || 0) : "—")
      + stat("AI FPS", online ? (i.inference_fps ?? 0).toFixed(1) : "—")
      + stat("People", online ? (i.people ?? 0) : "—")
      + stat("Vehicles", online ? (i.vehicles ?? 0) : "—")
    : stat("Status", esc(cameraStatusText(i.status || "—")), `s-status ${statusCls}`)
      + stat("People", online ? (i.people ?? 0) : "—")
      + stat("Vehicles", online ? (i.vehicles ?? 0) : "—");

  // Only keep a video connection open while the camera is online.
  const img = el.querySelector("img");
  const ov = el.querySelector(".cam-overlay");
  if (online && img.dataset.want !== "1") {
    img.dataset.want = "1";
    img.src = `/video/${i.id}?t=${Date.now()}`;
  } else if (!online && img.dataset.want === "1") {
    img.dataset.want = "0";
    img.removeAttribute("src");
  }
  ov.hidden = online;
  if (!online) {
    ov.querySelector(".ov-title").textContent = ui.tech
      ? `${i.id.toUpperCase().replace("_", " ")}: ${i.status === "CONNECTION LOST" ? "CONNECTION LOST"
        : i.status === "DISABLED" ? "CAMERA DISABLED" : i.status === "CONNECTING" ? "CONNECTING…" : "CAMERA OFFLINE"}`
      : `${i.name || i.id}: ${cameraStatusText(i.status)}`;
    ov.querySelector(".ov-detail").textContent = cameraHelp(i);
  }
  updateSituation();
}

function flashCamera(id) {
  const c = cams.get(id);
  if (!c) return;
  c.el.classList.add("alerting");
  clearTimeout(c.flashTimer);
  c.flashTimer = setTimeout(() => c.el.classList.remove("alerting"), 6000);
}

// ------------------------------------------------------------------- alerts
export async function loadRecentAlerts() {
  const { events } = await api("/api/events?limit=25");
  events.reverse().forEach((e) => addOrUpdateAlert(e, false));
}

export function handleEvent(msg) {
  const e = msg.event;
  if (!e || !e.id) return;
  const isNew = msg.action === "created";
  addOrUpdateAlert(e, isNew);
  if (isNew) {
    if (["critical", "high"].includes(e.severity)) {
      flashCamera(e.camera_id);
      beep(e.severity);
    }
    if (e.event_type === "PERSON_DETECTED") located += 1;
    toast(`<b>${eventIcon(e.event_type)} ${esc(eventTitle(e.event_type))}</b><br><span class="muted">${esc(e.camera_name || e.camera_id)} · ${certainty(e.confidence)}</span>`,
      e.severity);
  }
  updateSituation();
}

export function rerenderAlerts() { alerts.forEach((a) => renderAlert(a, false)); }

function addOrUpdateAlert(e, flash) {
  const list = document.getElementById("alertList");
  list.querySelector(".empty")?.remove();
  let a = alerts.get(e.id);
  if (!a) {
    const el = document.createElement("div");
    a = { event: e, el };
    alerts.set(e.id, a);
    list.prepend(el);
    if (alerts.size > MAX_ALERTS) {
      const oldest = [...alerts.keys()][0];
      alerts.get(oldest).el.remove();
      alerts.delete(oldest);
    }
  }
  a.event = e;
  renderAlert(a, flash);
  const active = [...alerts.values()].filter((x) => ["critical", "high"].includes(x.event.severity)
    && x.event.status !== "RESOLVED").length;
  const badge = document.getElementById("alertCount");
  badge.textContent = alerts.size;
  badge.classList.toggle("hot", active > 0);
}

function renderAlert(a, flash) {
  const e = a.event;
  const bits = [`<span>${esc(e.camera_name || e.camera_id)}</span>`, `<span>${T("", "Confidence: ")}${certainty(e.confidence)}</span>`];
  if (e.tracking_id != null) bits.push(`<span>${T("Person #", "Person ID: ")}${e.tracking_id}</span>`);
  if (e.count != null) bits.push(`<span>${T(`${e.count} people`, `Count: ${e.count}`)}</span>`);
  if (e.duration_s) bits.push(`<span>${T("Lasted ", "Duration: ")}${Math.round(e.duration_s)}s</span>`);
  a.el.className = `alert sev-${e.severity}${e.status === "RESOLVED" ? " resolved" : ""}${flash ? " flash" : ""}`;
  a.el.innerHTML = `
    <div class="alert-top"><span class="alert-title">${eventIcon(e.event_type)} ${esc(eventTitle(e.event_type))}</span>
      <span class="alert-sev">${esc(priorityText(e.severity))}</span></div>
    <div class="alert-meta">${bits.join("")}</div>
    ${e.message ? `<div class="alert-msg">${esc(e.message)}</div>` : ""}
    <div class="alert-foot">
      <span class="alert-time">${clockTime(e.timestamp)}
        ${e.status !== "LOGGED" ? `<span class="status-tag ${esc(e.status)}">${esc(statusText(e.status))}</span>` : ""}</span>
      ${e.snapshot_path ? `<button class="btn small ghost">${T("See photo", "View Snapshot")}</button>` : ""}
    </div>`;
  a.el.querySelector("button")?.addEventListener("click", () => openEvent(e.id));
}

// ------------------------------------------------------ disaster situational
function updateSituation() {
  const panel = document.getElementById("saPanel");
  if (panel.hidden) return;
  const infos = [...cams.values()].map((c) => c.info).filter((i) => i.status === "ONLINE");
  document.getElementById("saPeople").textContent = infos.reduce((s, i) => s + (i.people || 0), 0);
  document.getElementById("saLocated").textContent = located;
  const act = [...alerts.values()].map((a) => a.event).filter((e) => e.status === "ACTIVE");
  document.getElementById("saFire").textContent =
    act.filter((e) => e.event_type === "FIRE_DETECTED" || e.event_type === "SMOKE_DETECTED").length;
  document.getElementById("saCrowd").textContent = act.filter((e) => e.event_type === "CROWD_ALERT").length;

  const shots = [...alerts.values()].map((a) => a.event)
    .filter((e) => e.snapshot_path && ["PERSON_DETECTED", "FIRE_DETECTED", "SMOKE_DETECTED", "CROWD_ALERT"].includes(e.event_type))
    .slice(-12).reverse();
  const g = document.getElementById("saGallery");
  if (!shots.length) return;
  g.innerHTML = shots.map((e) => `<figure data-id="${esc(e.id)}" title="${esc(eventTitle(e.event_type))}"><img src="${snapshotUrl(e.snapshot_path)}" alt="">
    <figcaption>${eventIcon(e.event_type)} ${clockTime(e.timestamp)}</figcaption></figure>`).join("");
  g.querySelectorAll("figure").forEach((f) => f.addEventListener("click", () => openEvent(f.dataset.id)));
}

export function setDisasterView(on) {
  document.getElementById("saPanel").hidden = !on;
  document.getElementById("disasterBanner").hidden = !on;
  updateSituation();
}

// --------------------------------------------------------------------- sound
let audio;
function beep(severity) {
  if (!soundOn) return;
  try {
    audio = audio || new (window.AudioContext || window.webkitAudioContext)();
    const osc = audio.createOscillator(), gain = audio.createGain();
    osc.frequency.value = severity === "critical" ? 880 : 660;
    gain.gain.setValueAtTime(0.08, audio.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.0001, audio.currentTime + 0.4);
    osc.connect(gain).connect(audio.destination);
    osc.start(); osc.stop(audio.currentTime + 0.4);
  } catch { /* audio not allowed until the user interacts */ }
}
