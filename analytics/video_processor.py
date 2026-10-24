"""
Per-camera analytics pipeline.

    newest frame -> frame sampling (PROCESS_FPS) -> YOLO -> ByteTrack
                 -> rule engine -> Event Manager

The processor also draws the overlay (boxes, class, confidence, track ID,
zones) used by the live MJPEG stream and by event snapshots.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

import cv2
import numpy as np

from .camera_manager import ONLINE, CameraSource
from .event_manager import EventManager
from .fire_detector import FireDetector
from .object_detector import PERSON, Detection, ObjectDetector
from .rule_engine import RuleEngine, zone_polygon_px
from .tracker import ByteTracker, TrackedObject

log = logging.getLogger(__name__)

# BGR colours
C_PERSON = (80, 220, 100)
C_VEHICLE = (255, 170, 60)
C_ALERT = (60, 60, 255)
C_FIRE = (0, 140, 255)
C_SMOKE = (170, 170, 170)
C_ZONE = (60, 60, 230)


@dataclass
class Overlay:
    tracks: list[TrackedObject] = field(default_factory=list)
    fire: list[Detection] = field(default_factory=list)
    in_zone: set[int] = field(default_factory=set)
    zones: list[dict] = field(default_factory=list)
    people: int = 0
    vehicles: int = 0


class CameraProcessor:
    def __init__(self, source: CameraSource, detector: ObjectDetector | None,
                 fire_detector: FireDetector, event_manager: EventManager,
                 get_zones: Callable[[str], list[dict]], is_disaster: Callable[[], bool],
                 settings):
        self.source = source
        self.detector = detector
        self.fire = fire_detector
        self.events = event_manager
        self.get_zones = get_zones
        self.is_disaster = is_disaster
        self.s = settings

        buffer = max(10, int(settings.process_fps * 3))  # keep lost IDs for ~3 s
        self.tracker = ByteTracker(high_thresh=settings.confidence_threshold,
                                   new_track_thresh=settings.confidence_threshold,
                                   track_buffer=buffer)
        self.rules = RuleEngine(settings.crowd_threshold, settings.loitering_seconds)

        self.overlay = Overlay()
        self._overlay_version = 0
        self._lock = threading.Lock()
        self._jpeg_cache: tuple[tuple[int, int], bytes] | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        self.frames_analysed = 0
        self.inference_fps = 0.0
        self.inference_ms = 0.0
        self.last_error: str | None = None

    # -------------------------------------------------------------- lifecycle
    def start(self):
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"ai-{self.source.id}", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)

    def stats(self) -> dict:
        return {
            "people": self.overlay.people,
            "vehicles": self.overlay.vehicles,
            "inference_fps": round(self.inference_fps, 1),
            "inference_ms": round(self.inference_ms, 1),
            "frames_analysed": self.frames_analysed,
            "ai_error": self.last_error,
        }

    # ------------------------------------------------------------------ loop
    def _run(self):
        interval = 1.0 / max(0.1, self.s.process_fps)
        last_frame_id = 0
        window_start, window_count = time.time(), 0
        error_logged_at = 0.0
        while not self._stop.is_set():
            started = time.time()
            frame_id, frame, _ = self.source.read()
            if frame is None or frame_id == last_frame_id or self.source.status != ONLINE:
                if self.source.status != ONLINE:
                    self._set_overlay(Overlay(zones=self.get_zones(self.source.id)))
                self._stop.wait(0.05)
                continue
            last_frame_id = frame_id
            try:
                self._process(frame)
                self.last_error = None
            except Exception as exc:  # keep the camera alive whatever happens
                self.last_error = str(exc)
                if time.time() - error_logged_at > 10:
                    log.exception("[%s] frame processing failed", self.source.id)
                    error_logged_at = time.time()

            self.frames_analysed += 1
            window_count += 1
            now = time.time()
            self.inference_ms = (now - started) * 1000
            if now - window_start >= 2.0:
                self.inference_fps = window_count / (now - window_start)
                window_start, window_count = now, 0
            # Frame sampling: analyse at most PROCESS_FPS frames per second.
            self._stop.wait(max(0.0, interval - (time.time() - started)))

    def _process(self, frame: np.ndarray):
        h, w = frame.shape[:2]
        zones = self.get_zones(self.source.id)
        disaster = self.is_disaster()

        tracks: list[TrackedObject] = []
        if self.detector is not None:
            tracks = self.tracker.update(self.detector.detect(frame))

        fire_dets = None
        every = 1 if disaster else max(1, self.s.fire_every_n_frames)
        if self.fire.available and self.frames_analysed % every == 0:
            fire_dets = self.fire.detect(frame)

        detection_ts = time.time()  # latency measurement starts here
        result = self.rules.evaluate(
            tracks, w, h, zones, detection_ts,
            person_events=disaster or self.s.person_events_in_security_mode)

        overlay = Overlay(tracks, fire_dets if fire_dets is not None else self.overlay.fire,
                          result.tracks_in_zone, zones, result.people_count, result.vehicle_count)
        self._set_overlay(overlay)

        self.events.handle(
            self.source.id, self.source.name, result, fire_dets,
            snapshot=lambda boxes: self.render(frame, overlay, highlight=boxes),
            detection_ts=detection_ts, disaster_mode=disaster)

    def _set_overlay(self, overlay: Overlay):
        with self._lock:
            self.overlay = overlay
            self._overlay_version += 1

    # --------------------------------------------------------------- drawing
    def jpeg(self) -> bytes | None:
        """Latest camera frame + latest AI overlay, JPEG-encoded (shared by all viewers)."""
        frame_id, frame, _ = self.source.read()
        if frame is None:
            return None
        with self._lock:
            key = (frame_id, self._overlay_version)
            if self._jpeg_cache and self._jpeg_cache[0] == key:
                return self._jpeg_cache[1]
            overlay = self.overlay
        img = self.render(frame, overlay, max_width=self.s.stream_width)
        ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, self.s.stream_jpeg_quality])
        if not ok:
            return None
        data = buf.tobytes()
        with self._lock:
            self._jpeg_cache = (key, data)
        return data

    def render(self, frame: np.ndarray, overlay: Overlay, max_width: int | None = None,
               highlight: list | None = None) -> np.ndarray:
        h, w = frame.shape[:2]
        scale = min(1.0, max_width / w) if max_width else 1.0
        img = cv2.resize(frame, (int(w * scale), int(h * scale))) if scale < 1 else frame.copy()
        ih, iw = img.shape[:2]
        fs = max(0.45, iw / 1400)  # font scale
        th = max(1, int(round(iw / 640)))  # line thickness

        # Zones (semi-transparent)
        if overlay.zones:
            layer = img.copy()
            for z in overlay.zones:
                poly = zone_polygon_px(z, iw, ih).astype(np.int32)
                if len(poly) >= 3:
                    cv2.fillPoly(layer, [poly], C_ZONE)
            img = cv2.addWeighted(layer, 0.18, img, 0.82, 0)
            for z in overlay.zones:
                poly = zone_polygon_px(z, iw, ih).astype(np.int32)
                if len(poly) >= 3:
                    cv2.polylines(img, [poly], True, C_ZONE, th + 1)
                    x, y = poly[0]
                    _label(img, z.get("name", z["id"]), (x + 4, y + 4), C_ZONE, fs, th, below=True)

        def sbox(b):
            return tuple(int(round(v * scale)) for v in b)

        for t in overlay.tracks:
            alert = t.track_id in overlay.in_zone
            color = C_ALERT if alert else (C_PERSON if t.class_name == PERSON else C_VEHICLE)
            x1, y1, x2, y2 = sbox(t.box)
            cv2.rectangle(img, (x1, y1), (x2, y2), color, th + (1 if alert else 0))
            _label(img, f"{t.class_name} {t.confidence:.2f} ID:{t.track_id}", (x1, y1), color, fs, th)

        for d in overlay.fire:
            color = C_FIRE if d.class_name == "fire" else C_SMOKE
            x1, y1, x2, y2 = sbox(d.box)
            cv2.rectangle(img, (x1, y1), (x2, y2), color, th + 1)
            _label(img, f"{d.class_name} {d.confidence:.2f}", (x1, y1), color, fs, th)

        for b in highlight or []:
            x1, y1, x2, y2 = sbox(b)
            cv2.rectangle(img, (x1 - 3, y1 - 3), (x2 + 3, y2 + 3), (0, 255, 255), th + 2)

        # Header: camera + time (+ DEMO MODE)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        header = f"{self.source.id.upper()}  {self.source.name}  |  {stamp}"
        _label(img, header, (8, 8), (30, 30, 30), fs, th, below=True, text_color=(255, 255, 255))
        info = f"People: {overlay.people}  Vehicles: {overlay.vehicles}"
        if self.source.is_demo:
            info = "DEMO MODE  |  " + info
        _label(img, info, (8, int(8 + 30 * fs * 1.6)), (30, 30, 30), fs, th, below=True,
               text_color=(0, 215, 255) if self.source.is_demo else (255, 255, 255))
        return img


def _label(img, text, org, color, fs, th, below=False, text_color=(255, 255, 255)):
    (tw, tht), base = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, fs, th)
    x = min(max(0, int(org[0])), max(0, img.shape[1] - tw - 8))
    y = int(org[1])
    if below:
        top = y
    else:
        top = max(0, y - tht - base - 6)
    cv2.rectangle(img, (x, top), (x + tw + 8, top + tht + base + 6), color, -1)
    cv2.putText(img, text, (x + 4, top + tht + 3), cv2.FONT_HERSHEY_SIMPLEX, fs, text_color, th,
                cv2.LINE_AA)
