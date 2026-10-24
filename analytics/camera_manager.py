"""
Stream Manager: connects to every camera and keeps the newest frame ready.

Supported inputs (config/cameras.json -> "type"):
  webcam  laptop / USB camera              "source": 0
  rtsp    smartphone or IP camera (RTSP)   "source": "rtsp://<phone-ip>:<port>/..."
  http    smartphone MJPEG over HTTP       "source": "http://<phone-ip>:8080/video"
  file    local video file = DEMO MODE     "source": "videos/demo.mp4"

Each camera has its own capture thread that reads continuously. Reading
continuously matters for network cameras: RTSP frames queue up in a buffer, and
if we only read when the AI is ready we would analyse old frames (added latency).
A dropped connection never crashes the app; the thread reports
"CONNECTION LOST" and reconnects every RECONNECT_SECONDS.
"""
from __future__ import annotations

import json
import logging
import os
import platform
import threading
import time
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from config.settings import BASE_DIR

log = logging.getLogger(__name__)

ONLINE, CONNECTING, OFFLINE, LOST, DISABLED = (
    "ONLINE", "CONNECTING", "OFFLINE", "CONNECTION LOST", "DISABLED")

PROTOCOL_LABEL = {"webcam": "USB / Local", "rtsp": "RTSP", "http": "HTTP (MJPEG)",
                  "file": "Local video file"}
VALID_TYPES = tuple(PROTOCOL_LABEL)


def configure_ffmpeg(rtsp_transport: str):
    """RTSP over TCP (reliable, in-order) or UDP (lower latency, may lose packets)."""
    transport = rtsp_transport if rtsp_transport in ("tcp", "udp") else "tcp"
    os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", f"rtsp_transport;{transport}")


class CameraSource:
    def __init__(self, cfg: dict, reconnect_seconds: float = 5.0,
                 on_status: Callable[[str, str, str | None], None] | None = None):
        self.cfg = dict(cfg)
        self.id: str = cfg["id"]
        self.name: str = cfg.get("name", self.id)
        self.type: str = cfg.get("type", "webcam")
        self.source = cfg.get("source", 0)
        self.enabled: bool = bool(cfg.get("enabled", True))
        self.loop: bool = bool(cfg.get("loop", True))
        self.reconnect_seconds = reconnect_seconds
        self.on_status = on_status

        self.status = DISABLED if not self.enabled else CONNECTING
        self.error: str | None = None
        self.width = self.height = 0
        self.capture_fps = 0.0
        self.frames_read = 0

        self._lock = threading.Lock()
        self._frame: np.ndarray | None = None
        self._frame_id = 0
        self._frame_ts = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ info
    @property
    def is_demo(self) -> bool:
        return self.type == "file"

    @property
    def protocol(self) -> str:
        return PROTOCOL_LABEL.get(self.type, self.type)

    def info(self) -> dict:
        return {
            "id": self.id, "name": self.name, "type": self.type, "source": self.source,
            "enabled": self.enabled, "protocol": self.protocol, "demo_mode": self.is_demo,
            "status": self.status, "error": self.error, "resolution": [self.width, self.height],
            "capture_fps": round(self.capture_fps, 1),
        }

    # -------------------------------------------------------------- lifecycle
    def start(self):
        if not self.enabled:
            self._set_status(DISABLED)
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"capture-{self.id}", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)

    def read(self) -> tuple[int, np.ndarray | None, float]:
        """Latest frame (id, image, capture time). Never blocks."""
        with self._lock:
            return self._frame_id, self._frame, self._frame_ts

    # ---------------------------------------------------------------- capture
    def _resolve_source(self):
        if self.type == "webcam":
            return int(self.source)
        if self.type == "file":
            p = Path(str(self.source))
            return str(p if p.is_absolute() else BASE_DIR / p)
        return str(self.source)

    def _open(self) -> cv2.VideoCapture | None:
        src = self._resolve_source()
        if self.type == "webcam":
            api = cv2.CAP_DSHOW if platform.system() == "Windows" else cv2.CAP_ANY
            cap = cv2.VideoCapture(src, api)
        elif self.type == "file":
            if not Path(src).exists():
                self.error = f"Video file not found: {self.source}"
                return None
            cap = cv2.VideoCapture(src)
        else:  # rtsp / http: network stream via FFmpeg, with timeouts
            params = [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 8000, cv2.CAP_PROP_READ_TIMEOUT_MSEC, 8000]
            cap = cv2.VideoCapture(src, cv2.CAP_FFMPEG, params)
        if not cap.isOpened():
            cap.release()
            self.error = {"webcam": f"Webcam {src} not available",
                          "file": f"Cannot open video {self.source}"}.get(
                self.type, f"Cannot connect to {self.source}")
            return None
        return cap

    def _run(self):
        while not self._stop.is_set():
            if self.status not in (OFFLINE, LOST):  # don't flicker while retrying
                self._set_status(CONNECTING)
            try:
                cap = self._open()
            except Exception as exc:  # malformed URL etc.
                cap, self.error = None, str(exc)
            if cap is None:
                log.warning("[%s] %s (retry in %ss)", self.id, self.error, self.reconnect_seconds)
                self._set_status(OFFLINE, self.error)
                self._stop.wait(self.reconnect_seconds)
                continue
            try:
                self._read_loop(cap)
            finally:
                cap.release()
            if not self._stop.is_set():
                log.warning("[%s] connection lost, reconnecting in %ss", self.id, self.reconnect_seconds)
                self._set_status(LOST, "Stream stopped delivering frames")
                self._stop.wait(self.reconnect_seconds)

    def _read_loop(self, cap: cv2.VideoCapture):
        video_fps = cap.get(cv2.CAP_PROP_FPS) if self.is_demo else 0
        frame_interval = 1.0 / video_fps if video_fps and video_fps < 120 else 0
        window_start, window_frames = time.time(), 0
        failures = 0
        while not self._stop.is_set():
            t0 = time.time()
            ok, frame = cap.read()
            if not ok or frame is None:
                if self.is_demo and self.loop and failures == 0:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)  # end of demo video: loop it
                failures += 1
                if failures >= 3:
                    return  # -> CONNECTION LOST, reconnect
                continue
            failures = 0
            now = time.time()
            with self._lock:
                self._frame, self._frame_ts = frame, now
                self._frame_id += 1
            self.frames_read += 1
            self.height, self.width = frame.shape[:2]
            if self.status != ONLINE:
                self.error = None
                self._set_status(ONLINE)
                log.info("[%s] %s ONLINE (%dx%d)", self.id, self.name, self.width, self.height)

            window_frames += 1
            if now - window_start >= 1.0:
                self.capture_fps = window_frames / (now - window_start)
                window_start, window_frames = now, 0
            if frame_interval:  # play video files at real speed
                self._stop.wait(max(0.0, frame_interval - (time.time() - t0)))

    def _set_status(self, status: str, error: str | None = None):
        changed = status != self.status
        self.status = status
        if error:
            self.error = error
        if status in (OFFLINE, LOST, DISABLED):
            self.capture_fps = 0.0
        if changed and self.on_status:
            try:
                self.on_status(self.id, status, self.error)
            except Exception:
                log.exception("status callback failed")


class CameraConfigStore:
    """Reads/writes config/cameras.json and config/zones.json."""

    def __init__(self, cameras_file: Path, zones_file: Path):
        self.cameras_file = Path(cameras_file)
        self.zones_file = Path(zones_file)
        self._lock = threading.Lock()

    def load_cameras(self) -> list[dict]:
        try:
            cams = json.loads(self.cameras_file.read_text())
        except FileNotFoundError:
            log.error("%s not found; no cameras configured", self.cameras_file)
            return []
        except json.JSONDecodeError as exc:
            log.error("Invalid JSON in %s: %s", self.cameras_file, exc)
            return []
        valid = []
        for c in cams:
            if "id" not in c or c.get("type", "webcam") not in VALID_TYPES:
                log.error("Skipping invalid camera entry: %s", c)
                continue
            valid.append(c)
        return valid

    def save_cameras(self, cameras: list[dict]):
        with self._lock:
            self.cameras_file.write_text(json.dumps(cameras, indent=2))

    def load_zones(self) -> dict[str, list[dict]]:
        try:
            return json.loads(self.zones_file.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def save_zones(self, zones: dict[str, list[dict]]):
        with self._lock:
            self.zones_file.write_text(json.dumps(zones, indent=2))
