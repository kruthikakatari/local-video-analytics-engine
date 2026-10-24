// System Health (plain) / Network & Performance (technical view).
// Every number here is measured by the running system.
import { api, esc, fmtNum, fmtBytes, bindTooltip } from "./api.js";
import { T, ui, cameraStatusText } from "./ui.js";

const ms = (v) => (v == null ? "—" : fmtNum(v, v < 10 ? 2 : 1));

function kpi(label, value, unit = "", sub = "") {
  return `<div class="kpi"><span>${label}</span><b>${value}${unit && value !== "—" ? `<small>${unit}</small>` : ""}</b>${sub ? `<em>${sub}</em>` : ""}</div>`;
}

function hbars(el, rows, formatter, altIndex = -1) {
  const max = Math.max(...rows.map((r) => r.value || 0), 1e-9);
  el.innerHTML = rows.map((r, i) => `
    <div class="hbar" data-i="${i}">
      <span class="hbar-label">${esc(r.label)}</span>
      <div class="hbar-track"><div class="hbar-fill${i === altIndex ? " alt" : ""}" style="width:${r.value ? Math.max(0.5, (r.value / max) * 100) : 0}%"></div></div>
      <span class="hbar-value">${formatter(r.value)}</span>
    </div>`).join("");
  el.querySelectorAll(".hbar").forEach((row) => bindTooltip(row, rows[row.dataset.i].tip));
}

export async function refreshMetrics() {
  const m = await api("/api/metrics");
  const L = m.latency, e2e = L.end_to_end_ms, mq = L.mqtt_ms;

  const v = m.video_mjpeg.bytes_per_second, q = m.mqtt.bytes_per_second;
  const cpu = m.resources ? m.resources.process_cpu_percent : null;

  document.getElementById("kpiGrid").innerHTML = (ui.tech ? [
    kpi("Events processed", fmtNum(m.events_processed)),
    kpi("MQTT messages received", fmtNum(m.mqtt.messages), "", `${fmtBytes(m.mqtt.bytes)} payload`),
    kpi("Avg alert latency", ms(e2e.avg), "ms", `${e2e.count} samples · detection → dashboard`),
    kpi("Min alert latency", ms(e2e.min), "ms", `median ${ms(e2e.median)} ms`),
    kpi("Max alert latency", ms(e2e.max), "ms", `p95 ${ms(e2e.p95)} ms`),
    kpi("Avg MQTT latency", ms(mq.avg), "ms", "publish → backend receive"),
    kpi("Avg event message size", fmtNum(m.mqtt.by_topic.events.avg_bytes), "B", "surveillance/events"),
    kpi("Events per minute", fmtNum(m.events_per_minute), "", "last 60 s"),
    kpi("Cameras online", `${m.cameras_online} / ${m.cameras_total}`),
    ...(m.resources ? [
      kpi("CPU (this app)", fmtNum(m.resources.process_cpu_percent, 0), "%", `system ${fmtNum(m.resources.system_cpu_percent)}% · ${m.resources.cpu_cores} cores`),
      kpi("RAM (this app)", fmtNum(m.resources.process_ram_mb), "MB", `system ${fmtNum(m.resources.system_ram_percent)}% used`),
    ] : []),
  ] : [
    kpi("Alerts recorded", fmtNum(m.events_processed), "", "since the history started"),
    kpi("Alerts in the last minute", fmtNum(m.events_per_minute)),
    kpi("Alert speed", e2e.avg == null ? "—" : fmtNum(e2e.avg, 0), "ms",
      e2e.avg == null ? "shown after the first alert" : "average time from spotting something to showing it here"),
    kpi("Fastest / slowest alert", e2e.count ? `${fmtNum(e2e.min, 0)} / ${fmtNum(e2e.max, 0)}` : "—", "ms",
      "1000 ms = 1 second"),
    kpi("Cameras connected", `${m.cameras_online} of ${m.cameras_total}`),
    kpi("Network data saved", v && q ? `${fmtNum(v / q, 0)}×` : "—", "",
      "less data by sending alerts instead of video"),
    ...(m.resources ? [
      kpi("Computer load", fmtNum(cpu, 0), "%", cpu > 85 ? "high: reduce cameras or checks per second" : "used by this app"),
      kpi("Memory used", fmtNum(m.resources.process_ram_mb), "MB", "by this app"),
    ] : []),
  ]).join("");

  const S = L.stages_ms;
  const stages = [
    ["Detection → MQTT publish", S.detection_to_publish, "Rule check, snapshot saved to disk, JSON built"],
    ["MQTT publish → receive", S.publish_to_receive, "Engine → Mosquitto broker → backend subscriber (TCP)"],
    ["MQTT receive → WebSocket", S.receive_to_websocket, "SQLite insert + hand-off to the web server"],
    ["WebSocket → dashboard", S.websocket_to_dashboard, "WebSocket frame to the browser (clock-synced)"],
  ];
  hbars(document.getElementById("stageChart"), stages.map(([label, st, what]) => ({
    label, value: st.avg,
    tip: `<b>${label}</b><br>${what}<br>avg ${ms(st.avg)} ms · min ${ms(st.min)} · max ${ms(st.max)}<br>${st.count} samples`,
  })), (v) => (v == null ? "—" : `${ms(v)} ms`));

  hbars(document.getElementById("trafficChart"), [
    { label: T("Live video to screens", "Video to dashboards (MJPEG)"), value: v,
      tip: `<b>Video (HTTP MJPEG)</b><br>${fmtBytes(m.video_mjpeg.bytes_sent)} sent in ${fmtNum(m.video_mjpeg.frames_sent)} frames<br>${m.video_mjpeg.active_streams} open streams` },
    { label: T("Alert messages", "All MQTT messages"), value: q,
      tip: `<b>MQTT (events + status)</b><br>${fmtNum(m.mqtt.messages)} messages, ${fmtBytes(m.mqtt.bytes)}` },
  ], (x) => `${fmtBytes(x)}/s`, 1);
  document.getElementById("trafficNote").innerHTML = v && q
    ? T(`Live video uses about <b>${fmtNum(v / q, 0)} times</b> more data than the alert messages.`,
      `Measured here: video traffic is <b>${fmtNum(v / q, 0)}×</b> the MQTT traffic. Averages since server start; application payload only (verify on the wire with Wireshark).`)
    : T("Keep the Live View open for a minute to measure this.",
      "Open the Live View tab for a while to collect video and MQTT byte counts.");

  document.querySelector("#camMetricTable tbody").innerHTML = m.cameras.map((c) => `
    <tr><td>${esc(T(c.name, `${c.id} — ${c.name}`))}</td><td>${esc(T(cameraStatusText(c.status), c.status))}</td>
      <td class="num">${fmtNum(c.capture_fps, ui.tech ? 1 : 0)}${T(" fps", "")}</td><td class="num">${fmtNum(c.inference_fps, 1)}</td>
      <td class="num tech-only">${fmtNum(c.inference_ms, 0)}</td><td class="num">${c.people}</td>
      <td class="tech-only">${c.resolution && c.resolution[0] ? `${c.resolution[0]}×${c.resolution[1]}` : "—"}</td></tr>`).join("");

  const topics = { events: "surveillance/events", alerts: "surveillance/alerts",
    camera_status: "surveillance/camera/+/status", system_status: "surveillance/system/status" };
  document.querySelector("#topicTable tbody").innerHTML = Object.entries(m.mqtt.by_topic).map(([k, t]) => `
    <tr><td><code>${topics[k] || k}</code></td><td class="num">${fmtNum(t.messages)}</td>
      <td class="num">${fmtNum(t.bytes)}</td><td class="num">${fmtNum(t.avg_bytes)} B</td></tr>`).join("");

  document.querySelector("#latencyTable tbody").innerHTML = L.recent.length ? L.recent.map((r) => `
    <tr><td class="mono">${esc(r.event_id)}</td><td>${esc(r.event_type || "")}</td><td>${esc(r.transport)}</td>
      <td class="num">${r.payload_bytes ?? "—"}</td><td class="num">${ms(r.mqtt_latency_ms)}</td>
      <td class="num">${ms(r.latency_ms)}</td></tr>`).join("")
    : `<tr><td colspan="6" class="empty">No events yet. Trigger a detection to measure latency.</td></tr>`;
}
