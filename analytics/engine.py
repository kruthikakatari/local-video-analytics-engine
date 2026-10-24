"""
AI engine running on the edge server (the laptop).

Owns the cameras, the AI models, the per-camera processors and the Event
Manager, and publishes everything it produces to MQTT:

  events         -> surveillance/events            (QoS 1: at-least-once)
  urgent alerts  -> surveillance/alerts            (QoS 1)
  camera status  -> surveillance/camera/<id>/status (QoS 0, retained)
  heartbeat      -> surveillance/system/status      (QoS 0, retained, Last Will)
"""
from __future__ import annotations

import logging
import os
import socket
import threading
import time
from typing import Callable

from mqtt.mqtt_client import MqttClient

from .camera_manager import (CameraConfigStore, CameraSource, ONLINE, VALID_TYPES,
                             configure_ffmpeg)
from .event_manager import CRITICAL, HIGH, EventManager
from .fire_detector import FireDetector
from .object_detector import ModelNotAvailable, ObjectDetector
from .video_processor import CameraProcessor

log = logging.getLogger(__name__)


class AnalyticsEngine:
    def __init__(self, settings, fallback: Callable[[dict], None] | None = None):
        """`fallback` receives events directly if the MQTT broker is unreachable,
        so no event is lost (they are marked transport="local-fallback")."""
        self.s = settings
        self.fallback = fallback
        self.store = CameraConfigStore(settings.cameras_file, settings.zones_file)
        self.zones = self.store.load_zones()
        self.disaster_mode = settings.disaster_mode
        self.detector: ObjectDetector | None = None
        self.detector_status, self.detector_message = "loading", "Loading YOLO model..."
        self.fire = FireDetector(None)
        self.sources: dict[str, CameraSource] = {}
        self.processors: dict[str, CameraProcessor] = {}
        self.started_at = time.time()
        self.running = False
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.fallback_deliveries = 0

        self.mqtt = MqttClient(
            client_id=f"edge-ai-engine-{socket.gethostname()}-{os.getpid()}",
            host=settings.mqtt_host, port=settings.mqtt_port,
            will=(settings.topic("system", "status"), {"ai_engine": "offline", "reason": "lost"}))
        self.events = EventManager(
            publish=self._publish_event, snapshot_dir=settings.snapshot_dir,
            cooldown_seconds=settings.event_cooldown_seconds,
            incident_clear_seconds=settings.incident_clear_seconds,
            incident_update_seconds=settings.incident_update_seconds,
            fire_confirm_frames=settings.fire_confirm_frames)

    # ------------------------------------------------------------- lifecycle
    def start(self):
        configure_ffmpeg(self.s.rtsp_transport)
        self.mqtt.start()
        self.running = True
        for cfg in self.store.load_cameras():
            if self.s.force_demo and cfg.get("type") == "file":
                cfg["enabled"] = True  # `python run.py --demo`
            self._add_camera(cfg, start_processor=False)
        # Loading models can take a few seconds: do it in the background so the
        # dashboard and live video are available immediately.
        threading.Thread(target=self._load_models_then_run, name="engine-start", daemon=True).start()
        threading.Thread(target=self._heartbeat, name="engine-heartbeat", daemon=True).start()

    def stop(self):
        self._stop.set()
        self.running = False
        for p in self.processors.values():
            p.stop()
        for src in self.sources.values():
            src.stop()
        self.mqtt.publish(self.s.topic("system", "status"),
                          {"ai_engine": "offline", "reason": "shutdown"}, retain=True)
        self.mqtt.stop()

    def _load_models_then_run(self):
        try:
            self.detector = ObjectDetector(self.s.yolo_model, self.s.detector_backend,
                                           self.s.img_size)
            self.detector_status, self.detector_message = "loaded", self.detector.name
        except (ModelNotAvailable, Exception) as exc:
            self.detector = None
            self.detector_status = "error"
            self.detector_message = f"YOLO model unavailable: {exc}"
            log.error("%s -- cameras will stream WITHOUT detection", self.detector_message)
        self.fire = FireDetector(self.s.fire_model_path, self.s.detector_backend, self.s.img_size,
                                 self.s.fire_confidence_threshold)
        with self._lock:
            for cam_id in list(self.sources):
                self._start_processor(cam_id)

    # --------------------------------------------------------------- cameras
    def _add_camera(self, cfg: dict, start_processor: bool = True):
        src = CameraSource(cfg, self.s.reconnect_seconds, on_status=self._on_camera_status)
        self.sources[src.id] = src
        src.start()
        if start_processor:
            self._start_processor(src.id)

    def _start_processor(self, cam_id: str):
        src = self.sources[cam_id]
        if not src.enabled or cam_id in self.processors:
            return
        proc = CameraProcessor(src, self.detector, self.fire, self.events,
                               get_zones=lambda cid: self.zones.get(cid, []),
                               is_disaster=lambda: self.disaster_mode, settings=self.s)
        self.processors[cam_id] = proc
        proc.start()

    def update_camera(self, cam_id: str, changes: dict) -> dict:
        """Change a camera (e.g. the phone's new IP address) and reconnect it."""
        allowed = {k: v for k, v in changes.items() if k in ("name", "type", "source", "enabled", "loop")}
        if "type" in allowed and allowed["type"] not in VALID_TYPES:
            raise ValueError(f"type must be one of {VALID_TYPES}")
        with self._lock:
            cams = self.store.load_cameras()
            cfg = next((c for c in cams if c["id"] == cam_id), None)
            if cfg is None:
                raise KeyError(cam_id)
            cfg.update(allowed)
            self.store.save_cameras(cams)
            if cam_id in self.processors:
                self.processors.pop(cam_id).stop()
            if cam_id in self.sources:
                self.sources.pop(cam_id).stop()
            self._add_camera(cfg, start_processor=self.detector_status != "loading")
        return self.camera_info(cam_id)

    def camera_info(self, cam_id: str) -> dict:
        src = self.sources[cam_id]
        info = src.info()
        proc = self.processors.get(cam_id)
        info.update(proc.stats() if proc else {"people": 0, "vehicles": 0, "inference_fps": 0,
                                               "inference_ms": 0, "frames_analysed": 0,
                                               "ai_error": None})
        info["zones"] = len(self.zones.get(cam_id, []))
        return info

    def cameras(self) -> list[dict]:
        return [self.camera_info(cid) for cid in sorted(self.sources)]

    def jpeg(self, cam_id: str) -> bytes | None:
        proc = self.processors.get(cam_id)
        if proc:
            return proc.jpeg()
        return None

    def raw_jpeg(self, cam_id: str) -> bytes | None:
        import cv2
        src = self.sources.get(cam_id)
        if not src:
            return None
        _, frame, _ = src.read()
        if frame is None:
            return None
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        return buf.tobytes() if ok else None

    # ----------------------------------------------------------------- zones
    def set_zones(self, cam_id: str, zones: list[dict]):
        with self._lock:
            self.zones[cam_id] = zones
            self.store.save_zones(self.zones)

    # ----------------------------------------------------------------- modes
    def set_disaster_mode(self, enabled: bool):
        self.disaster_mode = bool(enabled)
        log.warning("DISASTER MODE %s", "ENABLED" if enabled else "disabled")
        self._publish_system_status()

    # ------------------------------------------------------------ publishing
    def _publish_event(self, msg: dict):
        msg["publish_ts"] = round(time.time() * 1000, 3)
        sent = self.mqtt.publish(self.s.topic("events"), msg, qos=self.s.mqtt_qos)
        if sent:
            if msg["action"] == "created" and msg["severity"] in (HIGH, CRITICAL):
                alert = {k: msg[k] for k in ("event_id", "camera_id", "event_type", "severity",
                                             "confidence", "tracking_id", "timestamp")}
                self.mqtt.publish(self.s.topic("alerts"), alert, qos=self.s.mqtt_qos)
            log.info("EVENT %-8s %-16s %s conf=%.2f (%d B via MQTT)", msg["action"],
                     msg["event_type"], msg["camera_id"], msg["confidence"], sent)
            return
        # Broker unreachable: deliver in-process so the event is still stored & shown.
        msg["transport"] = "local-fallback"
        msg["publish_ts"] = None
        self.fallback_deliveries += 1
        log.warning("MQTT offline -> event %s delivered via local fallback", msg["event_id"])
        if self.fallback:
            self.fallback(msg)

    def _on_camera_status(self, cam_id: str, status: str, error: str | None):
        self._publish_camera_status(cam_id)

    def _publish_camera_status(self, cam_id: str):
        if cam_id not in self.sources:
            return
        i = self.camera_info(cam_id)
        payload = {"camera_id": cam_id, "status": i["status"], "error": i["error"],
                   "capture_fps": i["capture_fps"], "inference_fps": i["inference_fps"],
                   "people": i["people"], "vehicles": i["vehicles"], "ts": round(time.time() * 1000)}
        self.mqtt.publish(self.s.topic("camera", cam_id, "status"), payload, qos=0, retain=True)

    def status(self) -> dict:
        return {
            "ai_engine": "running" if self.running else "stopped",
            "detector": self.detector_status, "detector_message": self.detector_message,
            "fire_model": self.fire.status, "fire_message": self.fire.message,
            "disaster_mode": self.disaster_mode,
            "cameras_online": sum(1 for s in self.sources.values() if s.status == ONLINE),
            "cameras_total": len(self.sources),
            "events_created": self.events.events_created,
            "mqtt_connected": self.mqtt.connected,
            "uptime_s": round(time.time() - self.started_at),
        }

    def _publish_system_status(self):
        payload = self.status()
        payload["ts"] = round(time.time() * 1000)
        self.mqtt.publish(self.s.topic("system", "status"), payload, qos=0, retain=True)

    def _heartbeat(self):
        tick = 0
        while not self._stop.wait(2.0):
            for cam_id in list(self.sources):
                self._publish_camera_status(cam_id)
            if tick % 2 == 0:
                self._publish_system_status()
            tick += 1
