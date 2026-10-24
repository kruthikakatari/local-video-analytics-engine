"""
MQTT client (paho-mqtt) used by both sides of the system:

  AI engine  --PUBLISH-->  Mosquitto broker  --PUBLISH-->  FastAPI backend
  (publisher)              (localhost:1883 or LAN IP)      (subscriber)

Topics
------
  surveillance/events                     every event message (create/update/resolve)
  surveillance/alerts                     only new HIGH/CRITICAL events (e.g. for a phone MQTT app)
  surveillance/camera/{camera_id}/status  camera status + FPS + people count (retained)
  surveillance/system/status              AI engine heartbeat (retained, with Last Will)

Only small JSON messages travel over MQTT. Video never does: video uses
RTSP (camera -> laptop) and HTTP/MJPEG (laptop -> browser).
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Callable

import paho.mqtt.client as mqtt

log = logging.getLogger(__name__)


def encode(payload: dict) -> bytes:
    """Compact JSON (no spaces) to keep messages small on the network."""
    return json.dumps(payload, separators=(",", ":"), default=str).encode()


class MqttClient:
    def __init__(self, client_id: str, host: str, port: int, keepalive: int = 30,
                 will: tuple[str, dict] | None = None):
        self.client_id, self.host, self.port = client_id, host, port
        self.connected = False
        self.last_error: str | None = None
        self._subs: list[tuple[str, int]] = []
        self._handlers: list[tuple[str, Callable[[str, bytes, float], None]]] = []
        self._lock = threading.Lock()
        self.stats = {"published": 0, "published_bytes": 0, "received": 0, "received_bytes": 0}

        self.client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
                                  client_id=client_id, clean_session=True)
        self.client.reconnect_delay_set(min_delay=1, max_delay=10)
        if will:  # broker publishes this if we disconnect unexpectedly (Last Will & Testament)
            self.client.will_set(will[0], encode(will[1]), qos=1, retain=True)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.keepalive = keepalive

    # ----------------------------------------------------------- connection
    def start(self):
        """Non-blocking: connects in the background and keeps retrying if the broker is down."""
        try:
            self.client.connect_async(self.host, self.port, self.keepalive)
            self.client.loop_start()
        except Exception as exc:
            self.last_error = str(exc)
            log.error("MQTT start failed: %s", exc)

    def stop(self):
        try:
            self.client.disconnect()
            self.client.loop_stop()
        except Exception:
            pass

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            self.connected = False
            self.last_error = f"Broker refused connection: {reason_code}"
            log.error(self.last_error)
            return
        self.connected, self.last_error = True, None
        log.info("MQTT connected to %s:%s as %s", self.host, self.port, self.client_id)
        for topic, qos in self._subs:  # re-subscribe after every reconnect
            client.subscribe(topic, qos)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        if self.connected:
            log.warning("MQTT disconnected from %s:%s (%s)", self.host, self.port, reason_code)
        self.connected = False
        self.last_error = f"Broker {self.host}:{self.port} unreachable"

    # ------------------------------------------------------------ messaging
    def subscribe(self, topic: str, handler: Callable[[str, bytes, float], None], qos: int = 1):
        self._subs.append((topic, qos))
        self._handlers.append((topic, handler))
        if self.connected:
            self.client.subscribe(topic, qos)

    def _on_message(self, client, userdata, msg):
        received_at = time.time()  # timestamp as early as possible (MQTT receive time)
        with self._lock:
            self.stats["received"] += 1
            self.stats["received_bytes"] += len(msg.payload)
        for pattern, handler in self._handlers:
            if mqtt.topic_matches_sub(pattern, msg.topic):
                try:
                    handler(msg.topic, msg.payload, received_at)
                except Exception:
                    log.exception("MQTT handler failed for %s", msg.topic)

    def publish(self, topic: str, payload: dict | bytes, qos: int = 0, retain: bool = False) -> int:
        """Returns the number of payload bytes sent, or 0 if not connected."""
        if not self.connected:
            return 0
        data = payload if isinstance(payload, bytes) else encode(payload)
        info = self.client.publish(topic, data, qos=qos, retain=retain)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            return 0
        with self._lock:
            self.stats["published"] += 1
            self.stats["published_bytes"] += len(data)
        return len(data)
