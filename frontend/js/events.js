// Alert History (stored in SQLite) + alert detail window.
import { api, esc, pct, snapshotUrl, fmtNum } from "./api.js";
import { T, ui, eventTitle, eventIcon, priorityText, statusText, dateTime } from "./ui.js";

const PAGE = 25;
let offset = 0;
let total = 0;
let debounce;

export function initEvents(cameras) {
  const sel = document.getElementById("fCamera");
  sel.innerHTML = `<option value="">All cameras</option>` +
    cameras.map((c) => `<option value="${esc(c.id)}">${esc(c.name)}</option>`).join("");
  ["fCamera", "fType", "fSeverity", "fDate"].forEach((id) =>
    document.getElementById(id).addEventListener("change", () => { offset = 0; loadEvents(); }));
  document.getElementById("fSearch").addEventListener("input", () => {
    clearTimeout(debounce);
    debounce = setTimeout(() => { offset = 0; loadEvents(); }, 300);
  });
  document.getElementById("fReset").addEventListener("click", () => {
    ["fCamera", "fType", "fSeverity", "fDate", "fSearch"].forEach((id) => { document.getElementById(id).value = ""; });
    offset = 0; loadEvents();
  });
  document.getElementById("prevPage").addEventListener("click", () => { offset = Math.max(0, offset - PAGE); loadEvents(); });
  document.getElementById("nextPage").addEventListener("click", () => { if (offset + PAGE < total) { offset += PAGE; loadEvents(); } });
  document.getElementById("modalClose").addEventListener("click", closeModal);
  document.getElementById("modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });
}

export async function loadEvents() {
  const p = new URLSearchParams({ limit: PAGE, offset });
  const add = (k, id) => { const v = document.getElementById(id).value; if (v) p.set(k, v); };
  add("camera_id", "fCamera"); add("event_type", "fType"); add("severity", "fSeverity");
  add("date", "fDate"); add("q", "fSearch");
  const data = await api(`/api/events?${p}`);
  total = data.total;
  const body = document.querySelector("#eventTable tbody");
  body.innerHTML = data.events.length ? data.events.map((e) => `<tr data-id="${esc(e.id)}">
      <td class="${ui.tech ? "mono" : ""}">${dateTime(e.timestamp)}</td>
      <td>${esc(e.camera_name || e.camera_id)}</td>
      <td>${eventIcon(e.event_type)} ${esc(T(eventTitle(e.event_type), e.event_type))}</td>
      <td><span class="sev-chip sev-${esc(e.severity)}">${esc(priorityText(e.severity))}</span></td>
      <td class="num">${pct(e.confidence)}</td>
      <td class="num">${e.tracking_id ?? "—"}</td>
      <td>${esc(statusText(e.status))}</td>
      <td>${e.snapshot_path ? `<img class="thumb" loading="lazy" src="${snapshotUrl(e.snapshot_path)}" alt="photo">` : "—"}</td>
    </tr>`).join("")
    : `<tr><td colspan="8" class="empty">${T("No alerts found. Try clearing the filters.", "No events match these filters.")}</td></tr>`;
  body.querySelectorAll("tr[data-id]").forEach((tr) => tr.addEventListener("click", () => openEvent(tr.dataset.id)));
  document.getElementById("eventTotal").textContent = `${fmtNum(total)} ${T("alert", "event")}${total === 1 ? "" : "s"}`;
  const page = Math.floor(offset / PAGE) + 1, pages = Math.max(1, Math.ceil(total / PAGE));
  document.getElementById("pageInfo").textContent = `Page ${page} of ${pages}`;
}

export async function openEvent(id) {
  let e;
  try { e = await api(`/api/events/${encodeURIComponent(id)}`); } catch { return; }
  const mt = e.metrics || {};
  const ms = (a, b) => (mt[a] != null && mt[b] != null ? `${fmtNum(mt[b] - mt[a], 2)} ms` : "—");
  const cell = ([k, v]) => `<div><span>${k}</span>${esc(v)}</div>`;

  let details;
  if (ui.tech) {
    const fields = [
      ["Event ID", e.id], ["Camera", `${e.camera_id} — ${e.camera_name || ""}`], ["Time", dateTime(e.timestamp)],
      ["Severity", e.severity], ["Confidence", pct(e.confidence)], ["Tracking ID", e.tracking_id ?? "—"],
      ["Zone", e.zone || "—"], ["Count", e.count ?? "—"], ["Status", e.status],
      ["Mode", e.mode], ["Duration", e.duration_s != null ? `${e.duration_s}s` : "—"], ["Transport", e.transport || "—"],
    ];
    const latency = [
      ["Detection → MQTT publish", ms("detection_timestamp", "mqtt_publish_timestamp")],
      ["MQTT publish → receive", ms("mqtt_publish_timestamp", "mqtt_receive_timestamp")],
      ["MQTT receive → WebSocket", ms("mqtt_receive_timestamp", "websocket_timestamp")],
      ["WebSocket → dashboard", ms("websocket_timestamp", "dashboard_receive_timestamp")],
      ["End-to-end alert latency", mt.latency_ms != null ? `${fmtNum(mt.latency_ms, 2)} ms` : "—"],
      ["MQTT payload", mt.payload_bytes != null ? `${mt.payload_bytes} bytes` : "—"],
    ];
    details = `<div class="detail-grid">${fields.map(cell).join("")}</div>
      <h3 style="font-size:13px;margin:18px 0 8px">Measured latency</h3>
      <div class="detail-grid">${latency.map(cell).join("")}</div>
      ${e.snapshot_path ? `<p class="note">Snapshot file: <code>${esc(e.snapshot_path)}</code></p>` : ""}`;
  } else {
    const fields = [
      ["When", dateTime(e.timestamp)], ["Camera", e.camera_name || e.camera_id],
      ["Priority", priorityText(e.severity)], ["How sure", pct(e.confidence)],
    ];
    if (e.tracking_id != null) fields.push(["Person", `#${e.tracking_id}`]);
    if (e.zone) fields.push(["Area", e.zone]);
    if (e.count != null) fields.push(["People", e.count]);
    if (e.status !== "LOGGED") fields.push(["Status", statusText(e.status)]);
    if (e.duration_s) fields.push(["How long", `${Math.round(e.duration_s)} seconds`]);
    const speed = mt.latency_ms != null
      ? `This alert reached the screen ${fmtNum(mt.latency_ms, 0)} milliseconds after it was detected.` : "";
    details = `<div class="detail-grid">${fields.map(cell).join("")}</div>
      ${speed ? `<p class="note">${speed}</p>` : ""}`;
  }

  document.getElementById("modalBody").innerHTML = `
    <h2 style="font-size:18px">${eventIcon(e.event_type)} ${esc(eventTitle(e.event_type))}</h2>
    <p class="muted" style="margin:4px 0 0">${esc(e.message || "")}</p>
    ${e.snapshot_path ? `<img class="snapshot" src="${snapshotUrl(e.snapshot_path)}" alt="Photo taken when this happened">`
      : `<p class="empty">${T("No photo was saved for this alert.", "No snapshot stored for this event.")}</p>`}
    ${details}`;
  document.getElementById("modal").hidden = false;
}

function closeModal() { document.getElementById("modal").hidden = true; }
