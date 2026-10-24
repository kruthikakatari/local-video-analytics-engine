"""
MQTT -> Backend -> SQLite + WebSocket bridge.

The backend is an MQTT *subscriber*. For every event message it:
  1. timestamps the arrival (MQTT receive time)
  2. stores / updates the event in SQLite
  3. stores the latency timestamps in the metrics table
  4. pushes the event to all dashboards over WebSocket
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from collections import deque

from mqtt.mqtt_client import MqttClient

from ..database import Database
from ..websocket_manager import WebSocketManager

log = logging.getLogger(__name__)


class EventService:
    def __init__(self, settings, db: Database, ws: WebSocketManager):
        self.s = settings
        self.db = db
        self.ws = ws
        self.loop: asyncio.AbstractEventLoop | None = None
        self.mqtt = MqttClient(client_id=f"backend-api-{int(time.time())}",
                               host=settings.mqtt_host, port=settings.mqtt_port)
        self.camera_status: dict[str, dict] = {}
        self.system_status: dict = {}
        self._lock = threading.Lock()
        self._event_times: deque[float] = deque(maxlen=10000)
        # Application-level MQTT counters (per topic group)
        self.counters = {
            "events": {"messages": 0, "bytes": 0},
            "alerts": {"messages": 0, "bytes": 0},
            "camera_status": {"messages": 0, "bytes": 0},
            "system_status": {"messages": 0, "bytes": 0},
        }
        self.fallback_events = 0
        self.started_at = time.time()

    def start(self, loop: asyncio.AbstractEventLoop):
        self.loop = loop
        t = self.s.topic
        self.mqtt.subscribe(t("events"), self._on_event, qos=self.s.mqtt_qos)
        self.mqtt.subscribe(t("alerts"), self._on_alert, qos=self.s.mqtt_qos)
        self.mqtt.subscribe(t("camera", "+", "status"), self._on_camera_status, qos=0)
        self.mqtt.subscribe(t("system", "status"), self._on_system_status, qos=0)
        self.mqtt.start()

    def stop(self):
        self.mqtt.stop()

    def _count(self, group: str, payload: bytes):
        with self._lock:
            self.counters[group]["messages"] += 1
            self.counters[group]["bytes"] += len(payload)

    # ------------------------------------------------------------- handlers
    def _on_event(self, topic: str, payload: bytes, received_at: float):
        self._count("events", payload)
        try:
            msg = json.loads(payload)
        except json.JSONDecodeError:
            log.warning("Ignoring malformed event on %s", topic)
            return
        self._process(msg, received_at * 1000, len(payload), transport="mqtt")

    def handle_fallback(self, msg: dict):
        """Direct delivery from the AI engine when the broker is down."""
        self.fallback_events += 1
        self._process(msg, None, 0, transport="local-fallback")

    def _process(self, msg: dict, receive_ms: float | None, size: int, transport: str):
        msg["transport"] = transport
        msg["mqtt_receive_ts"] = round(receive_ms, 3) if receive_ms else None
        is_new = self.db.save_event(msg)
        action = msg.get("action", "created")
        if action == "created":
            if not is_new:
                return  # duplicate delivery (QoS 1) -> already handled
            with self._lock:
                self._event_times.append(time.time())
            self.db.save_metric(msg["event_id"], msg.get("detection_ts"), msg.get("publish_ts"),
                                receive_ms, None, size, transport)
        event = self.db.get_event(msg["event_id"]) or {}
        event.pop("metrics", None)
        out = {"type": "event", "action": action, "event": event,
               "timing": {"detection_ts": msg.get("detection_ts"),
                          "publish_ts": msg.get("publish_ts"),
                          "mqtt_receive_ts": msg.get("mqtt_receive_ts"),
                          "payload_bytes": size, "transport": transport}}
        if self.loop:
            on_sent = (lambda ts: self.db.set_websocket_ts(msg["event_id"], ts)) \
                if action == "created" else None
            self.ws.broadcast_threadsafe(self.loop, out, on_sent)

    def _on_alert(self, topic: str, payload: bytes, received_at: float):
        # Alerts duplicate high-severity events for external subscribers
        # (e.g. an MQTT app on a phone). Here we only count them.
        self._count("alerts", payload)

    def _on_camera_status(self, topic: str, payload: bytes, received_at: float):
        self._count("camera_status", payload)
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return
        cam_id = data.get("camera_id") or topic.split("/")[-2]
        previous = self.camera_status.get(cam_id, {}).get("status")
        self.camera_status[cam_id] = data
        if previous != data.get("status"):
            self.db.set_camera_status(cam_id, data.get("status", "UNKNOWN"))
        if self.loop:
            self.ws.broadcast_threadsafe(self.loop, {"type": "camera_status", **data})

    def _on_system_status(self, topic: str, payload: bytes, received_at: float):
        self._count("system_status", payload)
        try:
            self.system_status = json.loads(payload)
            self.system_status["received_at"] = time.time()
        except json.JSONDecodeError:
            return
        if self.loop:
            self.ws.broadcast_threadsafe(self.loop, {"type": "system_status", **self.system_status})

    # ------------------------------------------------------------- stats
    def events_per_minute(self) -> float:
        now = time.time()
        with self._lock:
            return float(sum(1 for t in self._event_times if now - t <= 60))

    def mqtt_totals(self) -> dict:
        with self._lock:
            msgs = sum(c["messages"] for c in self.counters.values())
            size = sum(c["bytes"] for c in self.counters.values())
            groups = {k: dict(v) for k, v in self.counters.items()}
        for g in groups.values():
            g["avg_bytes"] = round(g["bytes"] / g["messages"], 1) if g["messages"] else 0
        elapsed = max(1.0, time.time() - self.started_at)
        return {"messages": msgs, "bytes": size,
                "avg_bytes": round(size / msgs, 1) if msgs else 0,
                "bytes_per_second": round(size / elapsed, 1), "by_topic": groups}
