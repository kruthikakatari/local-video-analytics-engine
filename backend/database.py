"""
SQLite persistence (a single local file: database/surveillance.db).

Tables
------
cameras  camera configuration + last known status
events   one row per event (fire/crowd incidents are updated in place)
metrics  per-event timestamps for latency measurement:
         detection -> MQTT publish -> MQTT receive -> WebSocket send -> dashboard receive
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS cameras (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    type        TEXT NOT NULL,
    url         TEXT,
    status      TEXT,
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id            TEXT PRIMARY KEY,
    camera_id     TEXT NOT NULL,
    camera_name   TEXT,
    event_type    TEXT NOT NULL,
    severity      TEXT,
    confidence    REAL,
    tracking_id   INTEGER,
    zone          TEXT,
    count         INTEGER,
    message       TEXT,
    mode          TEXT,
    timestamp     TEXT NOT NULL,
    snapshot_path TEXT,
    status        TEXT,
    duration_s    REAL,
    updated_at    TEXT,
    transport     TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_time ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_camera ON events(camera_id);

CREATE TABLE IF NOT EXISTS metrics (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id                    TEXT UNIQUE NOT NULL,
    detection_timestamp         REAL,   -- epoch ms, AI engine clock
    mqtt_publish_timestamp      REAL,
    mqtt_receive_timestamp      REAL,
    websocket_timestamp         REAL,
    dashboard_receive_timestamp REAL,   -- converted to server clock (see websocket_manager)
    payload_bytes               INTEGER,
    mqtt_latency_ms             REAL,   -- receive - publish
    latency_ms                  REAL,   -- dashboard receive - detection (end-to-end)
    transport                   TEXT
);
"""

EVENT_COLUMNS = ["id", "camera_id", "camera_name", "event_type", "severity", "confidence",
                 "tracking_id", "zone", "count", "message", "mode", "timestamp",
                 "snapshot_path", "status", "duration_s", "updated_at", "transport"]


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # One connection shared by FastAPI worker threads and the MQTT thread,
        # serialised with a lock (simple and safe for this workload).
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self):
        with self._lock:
            self._conn.close()

    def ping(self) -> bool:
        try:
            with self._lock:
                self._conn.execute("SELECT 1").fetchone()
            return True
        except sqlite3.Error:
            return False

    def _exec(self, sql: str, params=()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _all(self, sql: str, params=()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def _one(self, sql: str, params=()) -> dict | None:
        with self._lock:
            r = self._conn.execute(sql, params).fetchone()
            return dict(r) if r else None

    # --------------------------------------------------------------- cameras
    def upsert_camera(self, cam: dict):
        self._exec(
            """INSERT INTO cameras (id, name, type, url, status, updated_at)
               VALUES (?, ?, ?, ?, ?, datetime('now','localtime'))
               ON CONFLICT(id) DO UPDATE SET name=excluded.name, type=excluded.type,
                 url=excluded.url, status=excluded.status, updated_at=excluded.updated_at""",
            (cam["id"], cam.get("name", cam["id"]), cam.get("type", ""), str(cam.get("source", "")),
             cam.get("status", "UNKNOWN")))

    def set_camera_status(self, cam_id: str, status: str):
        self._exec("UPDATE cameras SET status=?, updated_at=datetime('now','localtime') WHERE id=?",
                   (status, cam_id))

    def cameras(self) -> list[dict]:
        return self._all("SELECT * FROM cameras ORDER BY id")

    # ---------------------------------------------------------------- events
    def save_event(self, msg: dict) -> bool:
        """Insert a new event, or update an existing incident. Returns True if new.
        (MQTT QoS 1 can deliver a message twice; the primary key makes that harmless.)"""
        row = {
            "id": msg["event_id"], "camera_id": msg["camera_id"],
            "camera_name": msg.get("camera_name"), "event_type": msg["event_type"],
            "severity": msg.get("severity"), "confidence": msg.get("confidence"),
            "tracking_id": msg.get("tracking_id"), "zone": msg.get("zone"),
            "count": msg.get("count"), "message": msg.get("message"), "mode": msg.get("mode"),
            "timestamp": msg["timestamp"], "snapshot_path": msg.get("snapshot_path"),
            "status": msg.get("status"), "duration_s": msg.get("duration_s"),
            "updated_at": msg.get("resolved_at") or msg["timestamp"],
            "transport": msg.get("transport", "mqtt"),
        }
        cols = ", ".join(EVENT_COLUMNS)
        marks = ", ".join("?" for _ in EVENT_COLUMNS)
        cur = self._exec(f"INSERT OR IGNORE INTO events ({cols}) VALUES ({marks})",
                         [row[c] for c in EVENT_COLUMNS])
        if cur.rowcount:
            return True
        if msg.get("action") in ("updated", "resolved"):
            self._exec(
                """UPDATE events SET confidence=?, count=?, message=?, status=?, duration_s=?,
                   updated_at=datetime('now','localtime') WHERE id=?""",
                (row["confidence"], row["count"], row["message"], row["status"],
                 row["duration_s"], row["id"]))
        return False

    def get_event(self, event_id: str) -> dict | None:
        ev = self._one("SELECT * FROM events WHERE id=?", (event_id,))
        if ev:
            ev["metrics"] = self._one("SELECT * FROM metrics WHERE event_id=?", (event_id,))
        return ev

    def list_events(self, camera_id=None, event_type=None, severity=None, date=None,
                    status=None, search=None, limit=100, offset=0) -> tuple[list[dict], int]:
        where, params = [], []
        for col, val in (("camera_id", camera_id), ("event_type", event_type),
                         ("severity", severity), ("status", status)):
            if val:
                where.append(f"{col} = ?")
                params.append(val)
        if date:  # YYYY-MM-DD
            where.append("substr(timestamp, 1, 10) = ?")
            params.append(date)
        if search:
            where.append("(message LIKE ? OR id LIKE ? OR camera_name LIKE ?)")
            params += [f"%{search}%"] * 3
        clause = f"WHERE {' AND '.join(where)}" if where else ""
        total = self._one(f"SELECT COUNT(*) AS n FROM events {clause}", params)["n"]
        rows = self._all(f"SELECT * FROM events {clause} ORDER BY timestamp DESC LIMIT ? OFFSET ?",
                         params + [int(limit), int(offset)])
        return rows, total

    def active_events(self) -> list[dict]:
        return self._all("SELECT * FROM events WHERE status='ACTIVE' ORDER BY timestamp DESC")

    def event_counts(self) -> dict:
        rows = self._all("SELECT event_type, COUNT(*) AS n FROM events GROUP BY event_type")
        return {r["event_type"]: r["n"] for r in rows}

    def count_events(self) -> int:
        return self._one("SELECT COUNT(*) AS n FROM events")["n"]

    # --------------------------------------------------------------- metrics
    def save_metric(self, event_id: str, detection_ts, publish_ts, receive_ts, ws_ts,
                    payload_bytes: int, transport: str):
        mqtt_latency = (receive_ts - publish_ts) if (publish_ts and receive_ts) else None
        self._exec(
            """INSERT OR IGNORE INTO metrics (event_id, detection_timestamp, mqtt_publish_timestamp,
               mqtt_receive_timestamp, websocket_timestamp, payload_bytes, mqtt_latency_ms, transport)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (event_id, detection_ts, publish_ts, receive_ts, ws_ts, payload_bytes,
             mqtt_latency, transport))

    def set_websocket_ts(self, event_id: str, ws_ts: float):
        self._exec("UPDATE metrics SET websocket_timestamp=? WHERE event_id=? AND "
                   "websocket_timestamp IS NULL", (ws_ts, event_id))

    def record_dashboard_receive(self, event_id: str, dashboard_ts: float) -> float | None:
        """First dashboard to acknowledge an event defines its end-to-end latency."""
        m = self._one("SELECT detection_timestamp, dashboard_receive_timestamp FROM metrics "
                      "WHERE event_id=?", (event_id,))
        if not m or m["dashboard_receive_timestamp"] is not None or m["detection_timestamp"] is None:
            return None
        latency = dashboard_ts - m["detection_timestamp"]
        self._exec("UPDATE metrics SET dashboard_receive_timestamp=?, latency_ms=? WHERE event_id=?",
                   (dashboard_ts, latency, event_id))
        return latency

    def latency_rows(self, limit: int = 5000) -> list[dict]:
        return self._all("SELECT m.*, e.event_type, e.camera_id FROM metrics m "
                         "LEFT JOIN events e ON e.id = m.event_id ORDER BY m.id DESC LIMIT ?",
                         (limit,))
