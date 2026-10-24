# Local Video Analytics & Security Engine

**An Offline, Edge-Based Surveillance System for Security and Disaster Management**
*(Computer Networks course-based project)*

Cameras stream over the local network to a laptop acting as the **edge server**. The laptop runs
YOLOv8n + ByteTrack and a rule engine, then publishes small JSON **events over MQTT**. A FastAPI
backend stores them in **SQLite** and pushes them to a **web dashboard over WebSockets**. Everything
runs without Internet.

Demo hardware: **laptop + smartphone (IP camera) + laptop webcam + Wi-Fi router with no Internet**
(or the laptop's own hotspot).

---

## 1. Architecture

```
 Smartphone (IP Webcam app) ──RTSP/RTP or HTTP──┐
 Laptop webcam ───────────────USB───────────────┤        LOCAL Wi-Fi / LAN  (no Internet)
 Demo video file (DEMO MODE) ───────────────────┤
                                                ▼
 ┌────────────────────────── EDGE SERVER (laptop) ──────────────────────────────┐
 │  Stream Manager (camera_manager.py)   one capture thread per camera         │
 │        ↓ newest frame                                                        │
 │  Frame sampling (PROCESS_FPS)          video_processor.py                    │
 │        ↓                                                                     │
 │  YOLOv8n detection (object_detector.py) ── Fire/smoke model (fire_detector)  │
 │        ↓                                                                     │
 │  ByteTrack tracking (tracker.py)       persistent IDs: Person #12, #13 …     │
 │        ↓                                                                     │
 │  Zone / rule engine (rule_engine.py)   intrusion, loitering, crowd, person   │
 │        ↓                                                                     │
 │  Event Manager (event_manager.py)      cooldown, de-dup, incidents, snapshot │──► snapshots/*.jpg
 │        ↓  JSON (~450 bytes)                                                  │
 │  MQTT publish ──► Mosquitto broker :1883 ──► MQTT subscribe (backend)        │
 │                                                 ↓                            │
 │                              FastAPI  ──► SQLite (events, metrics, cameras)  │
 │                                 │                                            │
 └─────────────────────────────────┼────────────────────────────────────────────┘
                                   │ HTTP :8000  (dashboard, REST API, MJPEG video)
                                   │ WebSocket /ws (instant alerts)
                                   ▼
             Browser dashboard on the laptop or on any phone on the same Wi-Fi
```

**Video and events never share a channel:**

| Data | Protocol | Path |
|---|---|---|
| Camera video | **RTSP/RTP** (phone) or USB (webcam) | camera → laptop |
| Live video in dashboard | **HTTP MJPEG** (`multipart/x-mixed-replace`) | laptop → browser |
| Events / alerts / status | **MQTT** (publish–subscribe, TCP :1883) | AI engine → broker → backend |
| Dashboard notifications | **WebSocket** (`/ws`) | backend → browser |

### Computer Networks concepts demonstrated

| Concept | Where |
|---|---|
| RTSP/RTP streaming, TCP vs UDP transport | `RTSP_TRANSPORT=tcp|udp` in `.env`; `analytics/camera_manager.py` |
| LAN / offline networking, edge computing | everything runs on the laptop; dashboard reachable at `http://<laptop-LAN-IP>:8000` |
| Publish/subscribe, broker, topics, wildcards | `mqtt/mqtt_client.py`, topics `surveillance/#`, `surveillance/camera/+/status` |
| MQTT QoS 0 vs QoS 1, retained messages, Last Will | events QoS 1; status retained; engine LWT on `surveillance/system/status` |
| Bandwidth reduction (events instead of video) | Metrics tab "Video vs event traffic" + Wireshark experiment 3 |
| Latency measurement per hop | timestamps at detection → publish → receive → WebSocket → dashboard |
| Clock synchronisation (NTP-style) | `frontend/js/socket.js` — makes latency correct on a phone with a different clock |
| Resilience: reconnects, timeouts | camera reconnect loop, MQTT auto-reconnect, WebSocket auto-reconnect, local fallback |

---

## 2. Project structure

```
local-video-analytics-engine/
├── run.py                     start everything:  python run.py
├── requirements.txt  .env.example
├── config/
│   ├── settings.py            reads .env
│   ├── cameras.json           camera list (edit the phone IP here or in the dashboard)
│   ├── zones.json             restricted zones (drawn in the dashboard)
│   └── mosquitto.conf         broker config that listens on the LAN
├── analytics/                 AI engine (edge)
│   ├── camera_manager.py      Stream Manager: webcam / RTSP / HTTP / file, reconnects
│   ├── video_processor.py     per-camera pipeline + overlay drawing
│   ├── object_detector.py     YOLOv8n (Ultralytics .pt or ONNX Runtime .onnx)
│   ├── tracker.py             ByteTrack (Kalman filter + two-stage IoU matching)
│   ├── fire_detector.py       separate, replaceable fire/smoke model
│   ├── rule_engine.py         intrusion, loitering, crowd, person located
│   ├── event_manager.py       central Event Manager (cooldown, incidents, snapshots)
│   └── engine.py              ties it together, publishes to MQTT
├── mqtt/mqtt_client.py        paho-mqtt wrapper (used by engine and backend)
├── backend/
│   ├── main.py                FastAPI app, WebSocket endpoint, static files
│   ├── database.py            SQLite: cameras, events, metrics
│   ├── websocket_manager.py
│   ├── routes/                cameras, events, metrics, system, video
│   └── services/              event_service (MQTT→DB→WS), metrics_service
├── frontend/                  dashboard (HTML, CSS, vanilla JS)
├── models/                    yolov8n.onnx (included), yolov8n.pt, fire_smoke.pt
├── videos/                    demo videos for DEMO MODE
├── snapshots/                 event snapshots (snapshots/YYYY-MM-DD/evt_….jpg)
├── database/                  surveillance.db
├── scripts/download_models.py
└── tests/                     pytest suite
```

---

## 3. Setup (do steps 1–6 once, while you still have Internet)

Requires **Python 3.9–3.12** (macOS: use `python3`; inside the activated .venv, `python` works too).

### Step 1 — Create a virtual environment
```bash
cd local-video-analytics-engine
python -m venv .venv
# Windows:        .venv\Scripts\activate
# macOS / Linux:  source .venv/bin/activate
```

### Step 2 — Install dependencies
```bash
pip install -r requirements.txt
```
(`ultralytics` installs PyTorch, ~1 GB. If that is a problem, see "ONNX-only install" below.)

### Step 3 — Install and start Mosquitto
* **Windows:** install from mosquitto.org/download. Mosquitto installs as a Windows service that
  only listens on localhost. For LAN access, stop the service (`net stop mosquitto` in an admin
  terminal) and run it with the project config:
  ```
  "C:\Program Files\mosquitto\mosquitto.exe" -c config\mosquitto.conf -v
  ```
* **Ubuntu/Debian:** `sudo apt install mosquitto mosquitto-clients` then
  `mosquitto -c config/mosquitto.conf -v`
* **macOS:** `brew install mosquitto` then `mosquitto -c config/mosquitto.conf -v`

Keep this terminal open. `-v` prints every connect/publish, which helps during the demo.

### Step 4 — Configure
```bash
copy .env.example .env      # Windows
cp .env.example .env        # macOS / Linux
```
Defaults work. Main settings: `YOLO_MODEL`, `FIRE_MODEL`, `CONFIDENCE_THRESHOLD`,
`CROWD_THRESHOLD`, `LOITERING_SECONDS`, `EVENT_COOLDOWN_SECONDS`, `PROCESS_FPS`.

### Step 5 — Add model files
```bash
python scripts/download_models.py     # saves models/yolov8n.pt (needs Internet once)
```
`models/yolov8n.onnx` is already included. To use it instead of the `.pt`, set
`YOLO_MODEL=models/yolov8n.onnx` (often faster on laptop CPUs).
Fire/smoke: put a YOLO fire/smoke model at `models/fire_smoke.pt` (see `models/README.md`).
Without it the app runs normally and shows **Fire Model: NOT CONFIGURED**.

### Step 6 — Configure cameras (`config/cameras.json`)
```json
[
  {"id": "camera_01", "name": "Laptop Webcam", "type": "webcam", "source": 0,  "enabled": true},
  {"id": "camera_02", "name": "Mobile Camera", "type": "rtsp",
   "source": "rtsp://192.168.1.50:8080/h264_ulaw.sdp", "enabled": true},
  {"id": "camera_03", "name": "Demo Video", "type": "file",
   "source": "videos/people-detection.mp4", "enabled": false}
]
```
`type`: `webcam` (device index), `rtsp`, `http` (MJPEG URL) or `file` (DEMO MODE).
The phone's IP can also be changed later from the dashboard (**Settings** tab).

### Step 7 — Start the application (now disconnect from the Internet)
```bash
python run.py            # webcam + phone
python run.py --demo     # also enables the demo-video camera (DEMO MODE)
```
The console prints the broker status and the dashboard URLs.

### Step 8 — Open the dashboard
* On the laptop: <http://localhost:8000>
* On a phone on the same Wi-Fi: `http://<laptop-LAN-IP>:8000` (printed by `run.py`)
* API docs: <http://localhost:8000/docs>

If a phone cannot open it, allow Python through the laptop firewall (Windows: "Allow an app
through firewall" → Python, Private networks) for ports **8000** and **1883**.

### Step 9 — Connect the smartphone camera
1. Connect the phone to the **same Wi-Fi** as the laptop.
2. Install an IP camera app. Example: **IP Webcam** (Android). Tap **Start server**; it shows
   an address like `http://192.168.1.50:8080`.
3. Use one of:
   * RTSP: `rtsp://192.168.1.50:8080/h264_ulaw.sdp` (type `rtsp`)
   * HTTP MJPEG: `http://192.168.1.50:8080/video` (type `http`)
   iPhone apps (e.g. "IP Camera Lite") show their own RTSP/HTTP URL; use that.
4. Enter it in **Settings → Camera 2 → Address → Save and connect**
   (or edit `config/cameras.json` and restart).
5. The card turns **LIVE / ONLINE** within a few seconds.

### ONNX-only install (no PyTorch)
Skip `ultralytics` in requirements, set `YOLO_MODEL=models/yolov8n.onnx`, and use an `.onnx`
fire model. Detection, tracking and everything else work the same.

---

## 4. Using the dashboard

* **Plain view vs Technical view** — by default the dashboard uses everyday words ("Alert delivery",
  "Person spotted", "93% sure"). Switch on **Technical view** (top right) to see the networking terms
  and measurements for the viva: MQTT, WebSocket, confidence scores, FPS, per-stage latency, payload
  sizes and the CSV export.
* **Status bar** — Backend, WebSocket, MQTT, Database, AI Engine, Fire Model and every camera,
  each 🟢 online / 🔴 offline (hover for details).
* **Live View** — one card per camera with live annotated video (boxes, class, confidence,
  track ID, e.g. `person 0.91 ID:12`), FPS, AI FPS, people and vehicle counts. A camera that
  drops shows **CAMERA 02: CONNECTION LOST** and reconnects by itself.
  **Active Alerts** appear instantly (WebSocket) with severity colour, confidence, person ID,
  time and **View Snapshot**.
* **Disaster Mode** (toggle, top right) — enables "person located" events for every new person,
  runs the fire/smoke model on every analysed frame, raises crowd severity, and shows the
  situational-awareness panel (people visible, people located, active fire/smoke and crowd
  incidents, snapshot gallery).
* **Alert History** — searchable table from SQLite, filter by camera, event type, severity and
  date; click a row for the snapshot and the measured latency of that event.
* **System Health** (switch on **Technical view**) — measured values only: events processed, MQTT messages, average /
  min / max / p95 alert latency, MQTT latency, latency per stage, FPS per camera, payload sizes
  per MQTT topic, video vs MQTT bytes/s, CPU and RAM. **Export CSV** downloads raw samples.
* **Settings** — edit camera sources; draw restricted zones by clicking points on a frame.

### Events

| Event | Trigger | Severity |
|---|---|---|
| `PERSON_DETECTED` | new tracked person (Disaster Mode, or `PERSON_EVENTS_IN_SECURITY_MODE=true`) | low (medium in disaster) |
| `INTRUSION` | a tracked person's feet enter a restricted zone | high |
| `LOITERING` | same person stays in a zone > `LOITERING_SECONDS` | medium |
| `CROWD_ALERT` | people in view > `CROWD_THRESHOLD` (incident) | medium (high in disaster) |
| `FIRE_DETECTED` | fire model, confirmed over `FIRE_CONFIRM_FRAMES` frames (incident) | critical |
| `SMOKE_DETECTED` | as above for smoke (incident) | high |

**De-duplication:** one event per (camera, type, track ID, zone), plus a per-zone cooldown
(`EVENT_COOLDOWN_SECONDS`). Fire, smoke and crowd are **incidents**: created once, the same event is
updated (max confidence, duration) at most every `INCIDENT_UPDATE_SECONDS`, and marked
**RESOLVED** after `INCIDENT_CLEAR_SECONDS` without detection. Ten seconds of fire = one event.

---

## 5. MQTT topics and messages

| Topic | QoS | Retained | Content |
|---|---|---|---|
| `surveillance/events` | 1 | no | every event: `created`, `updated`, `resolved` |
| `surveillance/alerts` | 1 | no | only new high/critical events (for a phone MQTT app) |
| `surveillance/camera/{camera_id}/status` | 0 | yes | status, FPS, people count (every 2 s) |
| `surveillance/system/status` | 0 | yes | engine heartbeat; Last Will = `{"ai_engine":"offline"}` |

Example event (compact JSON, ~450–480 bytes, no image data):
```json
{"event_id":"evt_20260928_203210_1eb8","camera_id":"camera_01","camera_name":"Laptop Webcam",
 "event_type":"INTRUSION","severity":"high","confidence":0.911,"tracking_id":12,
 "zone":"Restricted Area","count":null,"message":"Person #12 entered Restricted Area",
 "status":"LOGGED","mode":"security","timestamp":"2026-09-28T20:32:10.118",
 "snapshot_path":"snapshots/2026-09-28/evt_20260928_203210_1eb8.jpg",
 "detection_ts":1790612985118.159,"action":"created","publish_ts":1790612985129.517}
```
The snapshot is saved on the laptop; the message only carries its path.

Watch the traffic live from a terminal:
```bash
mosquitto_sub -h localhost -t "surveillance/#" -v
```
Or on a phone: install an MQTT client app (e.g. "MQTT Dash" / "MyMQTT"), connect to the laptop's
IP on port 1883 and subscribe to `surveillance/alerts`.

If the broker is down, the app keeps running: events are delivered to the backend through an
in-process **local fallback** (marked `transport = local-fallback` in the database), and the status
bar shows MQTT 🔴 OFFLINE until the broker is back (reconnection is automatic).

---

## 6. How latency is measured

| Timestamp | Taken where | Clock |
|---|---|---|
| `detection_timestamp` | right after YOLO + tracking + rules finish for the frame | laptop |
| `mqtt_publish_timestamp` | just before `client.publish()` | laptop |
| `mqtt_receive_timestamp` | first line of the backend's `on_message` | laptop |
| `websocket_timestamp` | just before the WebSocket frame is sent | laptop |
| `dashboard_receive_timestamp` | first line of the browser's `onmessage`, converted to server time | browser → synced |

* **Alert latency** = dashboard_receive − detection (end-to-end)
* **MQTT latency** = MQTT_receive − MQTT_publish

The browser may be a phone with a different clock, so it runs an NTP-style sync over the WebSocket
(`offset = server_ts − (t0 + t3)/2`, keeping the lowest round-trip sample) and reports its receive
time in the server's clock. All values are stored in the `metrics` table and shown on the metrics
tab; nothing is estimated. The latency covers the event path after the frame was analysed; the
capture/decoding and inference time per frame is reported separately as "Frame time (ms)".

---

## 7. Wireshark experiments

Install Wireshark (with **Npcap** on Windows; tick "support loopback traffic").
Capture on the **Wi-Fi interface** for traffic between phone and laptop, or on the
**loopback adapter** ("Adapter for loopback traffic capture" / `lo`) for traffic inside the laptop
(engine ↔ Mosquitto ↔ backend). Tip: to put MQTT on the Wi-Fi interface too, set
`MQTT_BROKER_HOST=<laptop LAN IP>` in `.env`.

### Experiment 1 — RTSP / video traffic
1. Start capture on the Wi-Fi interface. Filter: `rtsp || rtp || tcp.port == 8080`
2. Connect the phone camera (Step 9).
3. Observe the RTSP handshake (`OPTIONS → DESCRIBE → SETUP → PLAY`), then the continuous
   RTP/TCP video packets.
4. *Statistics → Conversations* or *I/O Graph*: note the bit rate (typically Mbit/s).
5. Repeat with `RTSP_TRANSPORT=udp` and compare (RTP over UDP, no retransmissions).

### Experiment 2 — MQTT traffic
1. Capture on loopback (or Wi-Fi if the broker is on the LAN IP). Filter: `mqtt`
2. Start the app: see `CONNECT`/`CONNACK`, `SUBSCRIBE`/`SUBACK`.
3. Trigger an intrusion: see `PUBLISH` on `surveillance/events`, followed by `PUBACK` (QoS 1).
4. Expand the packet: the payload is the JSON event (a few hundred bytes).
5. Kill the app abruptly (Task Manager → End task, or `kill -9`): after the keep-alive timeout the
   broker itself publishes the engine's **Last Will** on `surveillance/system/status`
   (`"reason":"lost"`). With a normal Ctrl+C the engine publishes `"reason":"shutdown"` itself.

### Experiment 3 — Continuous video vs event messages
1. With the phone streaming and the dashboard open, capture for 60 s.
2. *Statistics → Protocol Hierarchy*: compare bytes of RTP/RTSP (and HTTP MJPEG to browsers,
   `tcp.port == 8000`) with MQTT.
3. Compare with the application counters on the **System Health** tab (Technical view on)
   ("Video vs event traffic", "MQTT messages by topic").
4. Conclusion for the Results slide: bytes/s of video vs bytes/s of events, and the ratio.

### Experiment 4 — MQTT / event latency
1. Filter `mqtt.msgtype == 3` (PUBLISH). The engine's PUBLISH to the broker and the broker's
   PUBLISH to the backend are two packets; the difference of their timestamps is the broker hop.
2. Compare with **Avg MQTT latency** on the dashboard and with the per-event values in the event
   detail window / `Export CSV`.
3. Filter `websocket` (port 8000) to see the alert frame leaving for the browser.

---

## 8. Manual test sequence

| # | Test | Expected result |
|---|---|---|
| 1 | Start Mosquitto (`mosquitto -c config/mosquitto.conf -v`) | "Opening ipv4 listen socket on port 1883" |
| 2 | `python run.py` | "MQTT broker: OK", dashboard URLs printed |
| 3 | Open <http://localhost:8000> | Status bar: Backend, WebSocket, MQTT, Database 🟢 |
| 4 | Webcam | CAMERA 01 — LAPTOP WEBCAM shows LIVE video, FPS > 0 |
| 5 | Phone RTSP (Step 9) | CAMERA 02 — MOBILE CAMERA shows LIVE, protocol RTSP |
| 6 | Both cameras | Both 🟢 in the status bar; turn the phone app off → "CONNECTION LOST", on → back ONLINE |
| 7 | Stand in front of the webcam | green box `person 0.9x ID:n`, People: 1 |
| 8 | Walk around | the ID number stays the same while you are visible |
| 9 | Walk into the red "Restricted Area" (draw your own in Settings) | ⚠ INTRUSION DETECTED alert, camera card flashes red; stay → LOITERING after 10 s |
| 10 | `mosquitto_sub -t "surveillance/#" -v` | the event JSON appears on `surveillance/events` (and `surveillance/alerts`) |
| 11 | Dashboard | alert appears without refreshing the page |
| 12 | Event History tab / `sqlite3 database/surveillance.db "select * from events"` | the event row exists |
| 13 | View Snapshot | annotated JPEG from `snapshots/<date>/` |
| 14 | System Health tab (Technical view on) | alert latency and MQTT latency filled with measured values |
| 15 | Wireshark (section 7) | RTSP/RTP, MQTT and WebSocket packets captured |

Automated tests: `python -m pytest -q` (tracker, rules, event manager, SQLite, API/WebSocket,
detectors, full pipeline on the demo video, and a live MQTT round-trip when Mosquitto is running).

**Guaranteed demo without the phone:** `python run.py --demo` (the demo camera shows the
**DEMO MODE** label; the default zone on it produces INTRUSION events).

---

## 9. Filling in the Results slide

Run the demo for a few minutes, then read:

| PPT metric | Where to get it |
|---|---|
| Detection accuracy | count correct/missed/false detections on a test clip, or `yolo val` on a labelled set; the dashboard does not invent this |
| Processing speed (FPS per camera) | System Health → Cameras (Technical view on) (Capture FPS, AI FPS, frame time) |
| End-to-end alert latency | Avg / Min / Max / p95 alert latency tiles; raw samples via Export CSV |
| Bandwidth: raw stream vs events | Wireshark Experiment 3 (on-wire) + "Video vs event traffic" (application level) |
| Cameras supported simultaneously | number of cameras ONLINE while AI FPS stays near PROCESS_FPS |
| CPU / RAM usage | CPU / RAM tiles (psutil), or Task Manager |

---

## 10. API

| Method | Path | Description |
|---|---|---|
| GET | `/api/cameras` | cameras with live status, FPS, counts |
| PUT | `/api/cameras/{id}` | change name / type / source / enabled (reconnects) |
| GET/PUT | `/api/cameras/{id}/zones` | restricted zones (normalised polygon points) |
| GET | `/api/events` | history (`camera_id`, `event_type`, `severity`, `date`, `status`, `q`, `limit`, `offset`) |
| GET | `/api/events/active` | active incidents |
| GET | `/api/events/{id}` | one event + its latency metrics |
| GET | `/api/metrics` | all measured metrics |
| GET | `/api/metrics/export.csv` | latency samples |
| GET | `/api/system/status` | component status |
| POST | `/api/system/disaster-mode` | `{"enabled": true}` |
| GET | `/video/{camera_id}` | live MJPEG stream (annotated) |
| GET | `/video/{camera_id}/frame.jpg` | single frame (`?raw=true` without overlay) |
| WS | `/ws` | live events, camera status, latency, clock sync |

---

## 11. Troubleshooting

| Problem | Fix |
|---|---|
| MQTT 🔴 OFFLINE | start Mosquitto; check `MQTT_BROKER_HOST/PORT`; events still arrive via local fallback |
| Phone camera OFFLINE | same Wi-Fi? correct IP? open the `http://…:8080` page in the laptop browser first; try the `http` MJPEG URL; switch `RTSP_TRANSPORT` |
| Webcam OFFLINE | close other apps using the camera (Zoom, Teams); try `"source": 1` |
| AI Engine NO MODEL | run `python scripts/download_models.py` while online, or set `YOLO_MODEL=models/yolov8n.onnx` |
| Slow / high CPU | lower `PROCESS_FPS` (e.g. 3), `STREAM_FPS`, `STREAM_WIDTH`, use the `.onnx` model |
| Phone can't open dashboard | firewall: allow Python on private networks, ports 8000 and 1883 |
| Too many alerts | raise `CONFIDENCE_THRESHOLD`, `EVENT_COOLDOWN_SECONDS`, `CROWD_THRESHOLD` |

## 12. Scope

This prototype runs on one laptop. Raspberry Pi / Jetson deployment, mesh/LoRa networking, drones,
thermal cameras, federated learning, audio analytics, MQTT over TLS and cloud sync are
**future enhancements** (see the PPT) and are intentionally not implemented here.
