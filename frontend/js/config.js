// Settings: edit cameras (e.g. the phone's new address) and draw restricted areas (zones).
import { api, esc, toast } from "./api.js";
import { T, cameraStatusText, cameraHelp } from "./ui.js";

let zoneCam = null;
let points = [];      // normalised [x, y]
let zones = [];

// ------------------------------------------------------------------ cameras
const TYPE_OPTIONS = [
  ["webcam", "Laptop / USB camera", "webcam"],
  ["rtsp", "Phone or IP camera (RTSP address)", "rtsp"],
  ["http", "Phone camera (web address)", "http"],
  ["file", "Video file (demo)", "file"],
];
const SOURCE_HINT = {
  webcam: "Usually 0 for the built-in camera, 1 for a second camera",
  rtsp: "Copy it from the phone's camera app, e.g. rtsp://192.168.1.50:8080/h264_ulaw.sdp",
  http: "Copy it from the phone's camera app, e.g. http://192.168.1.50:8080/video",
  file: "A video in the videos folder, e.g. videos/people-detection.mp4",
};

export function renderCameraForms(cameras, onChanged) {
  const box = document.getElementById("cameraForms");
  box.innerHTML = cameras.map((c) => `
    <form class="cam-form" data-id="${esc(c.id)}">
      <div class="cam-form-head"><b>${esc(T(`Camera ${c.id.replace(/^camera_0*/, "")}`, c.id))}</b>
        <span class="muted small">${esc(T(cameraStatusText(c.status), c.status))}</span></div>
      <label>${T("Name", "Name")}<input name="name" value="${esc(c.name)}"></label>
      <label>${T("Camera type", "Type")}<select name="type">
        ${TYPE_OPTIONS.map(([v, simple, tech]) => `<option value="${v}" ${v === c.type ? "selected" : ""}>${T(simple, tech)}</option>`).join("")}
      </select></label>
      <label>${T("Address", "Source")}<input name="source" value="${esc(c.source)}" placeholder="0 | rtsp://… | http://… | videos/…">
        <span class="muted small hint">${esc(SOURCE_HINT[c.type] || "")}</span></label>
      <div class="actions">
        <label class="check"><input type="checkbox" name="enabled" ${c.enabled ? "checked" : ""}> ${T("Use this camera", "Enabled")}</label>
        <button class="btn primary small" type="submit">${T("Save and connect", "Save &amp; reconnect")}</button>
      </div>
      ${c.error ? `<div class="muted small">${T("Problem", "Last error")}: ${esc(T(cameraHelp(c), c.error))}</div>` : ""}
    </form>`).join("");
  box.querySelectorAll("form").forEach((f) => {
    const typeSel = f.querySelector("select[name=type]");
    typeSel.addEventListener("change", () => { f.querySelector(".hint").textContent = SOURCE_HINT[typeSel.value] || ""; });
    f.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const fd = new FormData(f);
      const body = { name: fd.get("name"), type: fd.get("type"), source: fd.get("source"),
        enabled: fd.get("enabled") === "on" };
      try {
        const info = await api(`/api/cameras/${f.dataset.id}`, { method: "PUT", body });
        toast(`<b>${esc(info.name)}</b> saved. Connecting…`);
        onChanged(info);
      } catch (e) { toast(`<b>Could not save.</b> ${esc(e.message)}`, "critical"); }
    });
  });

  const sel = document.getElementById("zoneCamera");
  const current = sel.value;
  sel.innerHTML = cameras.map((c) => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join("");
  if (current) sel.value = current;
}

// -------------------------------------------------------------- zone editor
export function initZoneEditor() {
  const sel = document.getElementById("zoneCamera");
  const canvas = document.getElementById("zoneCanvas");
  const img = document.getElementById("zoneImage");
  sel.addEventListener("change", () => selectZoneCamera(sel.value));
  img.addEventListener("load", draw);
  window.addEventListener("resize", draw);
  canvas.addEventListener("click", (e) => {
    const r = canvas.getBoundingClientRect();
    points.push([+((e.clientX - r.left) / r.width).toFixed(4), +((e.clientY - r.top) / r.height).toFixed(4)]);
    draw();
  });
  document.getElementById("zoneUndo").onclick = () => { points.pop(); draw(); };
  document.getElementById("zoneClear").onclick = () => { points = []; draw(); };
  document.getElementById("zoneRefresh").onclick = () => refreshFrame();
  document.getElementById("zoneSave").onclick = saveZone;
}

export async function selectZoneCamera(id) {
  if (!id) return;
  zoneCam = id;
  points = [];
  try { zones = await api(`/api/cameras/${id}/zones`); } catch { zones = []; }
  refreshFrame();
  renderZoneList();
}

function refreshFrame() {
  if (zoneCam) document.getElementById("zoneImage").src = `/video/${zoneCam}/frame.jpg?raw=true&t=${Date.now()}`;
}

function draw() {
  const canvas = document.getElementById("zoneCanvas");
  const r = canvas.getBoundingClientRect();
  canvas.width = r.width; canvas.height = r.height;
  const ctx = canvas.getContext("2d");
  const poly = (pts, stroke, fill) => {
    if (!pts.length) return;
    ctx.beginPath();
    pts.forEach(([x, y], i) => (i ? ctx.lineTo(x * r.width, y * r.height) : ctx.moveTo(x * r.width, y * r.height)));
    if (pts.length > 2) ctx.closePath();
    ctx.fillStyle = fill; if (pts.length > 2) ctx.fill();
    ctx.strokeStyle = stroke; ctx.lineWidth = 2; ctx.stroke();
  };
  zones.forEach((z) => poly(z.points, "#d03b3b", "rgba(208,59,59,0.18)"));
  poly(points, "#fab219", "rgba(250,178,25,0.18)");
  ctx.fillStyle = "#fab219";
  points.forEach(([x, y]) => { ctx.beginPath(); ctx.arc(x * r.width, y * r.height, 4, 0, Math.PI * 2); ctx.fill(); });
}

function renderZoneList() {
  const list = document.getElementById("zoneList");
  list.innerHTML = zones.length ? zones.map((z, i) => `
    <div class="zone-item"><span><b>${esc(z.name)}</b> <span class="muted small">${[z.intrusion ? "alerts on entry" : "", z.loitering ? "alerts if someone stays" : ""].filter(Boolean).join(" · ")}</span></span>
      <button class="btn small ghost" data-i="${i}">Delete</button></div>`).join("")
    : `<div class="muted small">No restricted areas for this camera yet.</div>`;
  list.querySelectorAll("button").forEach((b) => b.addEventListener("click", async () => {
    zones.splice(+b.dataset.i, 1);
    await pushZones();
  }));
  draw();
}

async function saveZone() {
  if (points.length < 3) { toast("Click at least 3 corners on the picture to mark the area.", "medium"); return; }
  zones.push({
    id: `zone_${Date.now().toString(36)}`,
    name: document.getElementById("zoneName").value || "Restricted Area",
    points, intrusion: document.getElementById("zoneIntrusion").checked,
    loitering: document.getElementById("zoneLoiter").checked, classes: ["person"],
  });
  points = [];
  await pushZones();
}

async function pushZones() {
  try {
    zones = await api(`/api/cameras/${zoneCam}/zones`, { method: "PUT", body: zones });
    toast("<b>Area saved.</b> It is active right away.");
  } catch (e) { toast(`<b>Could not save the area.</b> ${esc(e.message)}`, "critical"); }
  renderZoneList();
}
