"""
Centralised Event Manager.

Receives signals from the rule engine and the fire/smoke detector and decides
which ones become real events. It is the ONLY place where events are created.

De-duplication strategy
-----------------------
* One-shot events (INTRUSION, LOITERING, PERSON_DETECTED)
    - once per (camera, type, track ID, zone)       -> no repeat for the same person
    - plus a cooldown per (camera, type, zone)      -> no flood when IDs flicker
* Condition events (FIRE_DETECTED, SMOKE_DETECTED, CROWD_ALERT) are INCIDENTS:
    - created once (fire/smoke must be seen in FIRE_CONFIRM_FRAMES samples first)
    - while still detected -> the SAME event is updated (max confidence, duration)
      at most every INCIDENT_UPDATE_SECONDS
    - not seen for INCIDENT_CLEAR_SECONDS -> event resolved
  So 10 seconds of continuous fire = 1 event, not 50.

For every new event a single annotated snapshot is saved, then a compact JSON
message is handed to the publisher (MQTT). Video is never put in the message.
"""
from __future__ import annotations

import logging
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import cv2
import numpy as np

from .object_detector import Detection
from .rule_engine import (CROWD_ALERT, FIRE_DETECTED, INTRUSION, LOITERING, PERSON_DETECTED,
                          SMOKE_DETECTED, RuleResult, Signal)

log = logging.getLogger(__name__)

CRITICAL, HIGH, MEDIUM, LOW = "critical", "high", "medium", "low"
SEVERITY_ORDER = {CRITICAL: 3, HIGH: 2, MEDIUM: 1, LOW: 0}

ACTIVE, RESOLVED, LOGGED = "ACTIVE", "RESOLVED", "LOGGED"
CREATED, UPDATED = "created", "updated"


def severity_for(event_type: str, disaster_mode: bool) -> str:
    table = {
        FIRE_DETECTED: CRITICAL,
        SMOKE_DETECTED: HIGH,
        INTRUSION: HIGH,
        LOITERING: MEDIUM,
        CROWD_ALERT: HIGH if disaster_mode else MEDIUM,
        PERSON_DETECTED: MEDIUM if disaster_mode else LOW,
    }
    return table.get(event_type, LOW)


def new_event_id(now: float) -> str:
    return f"evt_{datetime.fromtimestamp(now).strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(2)}"


@dataclass
class Incident:
    event: dict
    started: float
    last_seen: float
    last_published: float


class EventManager:
    def __init__(self, publish: Callable[[dict], None], snapshot_dir: Path,
                 cooldown_seconds: float = 5.0, incident_clear_seconds: float = 10.0,
                 incident_update_seconds: float = 5.0, fire_confirm_frames: int = 2,
                 clock: Callable[[], float] = time.time):
        self.publish = publish
        self.snapshot_dir = Path(snapshot_dir)
        self.cooldown = cooldown_seconds
        self.clear_after = incident_clear_seconds
        self.update_every = incident_update_seconds
        self.fire_confirm_frames = max(1, fire_confirm_frames)
        self.clock = clock
        self._lock = threading.Lock()
        self._emitted: dict[tuple, float] = {}       # (cam, type, track, zone) -> time
        self._last_by_group: dict[tuple, float] = {}  # (cam, type, zone) -> time
        self._incidents: dict[tuple, Incident] = {}   # (cam, type) -> incident
        self._confirm: dict[tuple, int] = {}          # (cam, type) -> consecutive hits
        self.events_created = 0

    # ----------------------------------------------------------------- public
    def handle(self, camera_id: str, camera_name: str, rules: RuleResult,
               fire_detections: list[Detection] | None, snapshot: Callable[[list], np.ndarray],
               detection_ts: float, disaster_mode: bool = False) -> list[dict]:
        """Process one analysed frame. Returns the messages that were published."""
        out: list[dict] = []
        with self._lock:
            now = self.clock()
            for sig in rules.oneshots:
                msg = self._oneshot(camera_id, camera_name, sig, snapshot, detection_ts,
                                    disaster_mode, now)
                if msg:
                    out.append(msg)

            conditions = dict(rules.conditions)
            if fire_detections is not None:  # None = fire model not run on this frame
                conditions.update(self._fire_conditions(fire_detections))
            for etype, sig in conditions.items():
                msg = self._condition(camera_id, camera_name, etype, sig, snapshot,
                                      detection_ts, disaster_mode, now)
                if msg:
                    out.append(msg)
            self._prune(now)
        for msg in out:
            self.publish(msg)
        return out

    def active_incidents(self) -> list[dict]:
        with self._lock:
            return [dict(i.event) for i in self._incidents.values()]

    # --------------------------------------------------------------- one-shot
    def _oneshot(self, cam, cam_name, sig: Signal, snapshot, det_ts, disaster, now):
        key = (cam, sig.event_type, sig.tracking_id, sig.zone_id)
        group = (cam, sig.event_type, sig.zone_id)
        if key in self._emitted:
            return None
        if now - self._last_by_group.get(group, -1e9) < self.cooldown:
            return None
        self._emitted[key] = now
        self._last_by_group[group] = now
        event = self._build(cam, cam_name, sig, det_ts, disaster, LOGGED)
        event["snapshot_path"] = self._save_snapshot(event, snapshot, sig.boxes)
        self.events_created += 1
        return self._message(event, CREATED)

    # -------------------------------------------------------------- incidents
    def _fire_conditions(self, dets: list[Detection]) -> dict[str, Signal | None]:
        result: dict[str, Signal | None] = {}
        for kind, etype in (("fire", FIRE_DETECTED), ("smoke", SMOKE_DETECTED)):
            hits = [d for d in dets if d.class_name == kind]
            if hits:
                best = max(d.confidence for d in hits)
                result[etype] = Signal(etype, best, message=f"{kind.capitalize()} detected",
                                       boxes=[d.box for d in hits])
            else:
                result[etype] = None
        return result

    def _condition(self, cam, cam_name, etype, sig: Signal | None, snapshot, det_ts,
                   disaster, now):
        key = (cam, etype)
        inc = self._incidents.get(key)
        needed = self.fire_confirm_frames if etype in (FIRE_DETECTED, SMOKE_DETECTED) else 1

        if sig is None:
            self._confirm[key] = 0
            if inc and now - inc.last_seen >= self.clear_after:
                del self._incidents[key]
                inc.event.update(status=RESOLVED, resolved_at=_iso(now),
                                 duration_s=round(inc.last_seen - inc.started, 1))
                return self._message(inc.event, RESOLVED.lower())
            return None

        if inc is None:
            self._confirm[key] = self._confirm.get(key, 0) + 1
            if self._confirm[key] < needed:
                return None  # temporal confirmation: ignore single-frame flickers
            if now - self._last_by_group.get((cam, etype, None), -1e9) < self.cooldown:
                return None
            self._last_by_group[(cam, etype, None)] = now
            event = self._build(cam, cam_name, sig, det_ts, disaster, ACTIVE)
            event["snapshot_path"] = self._save_snapshot(event, snapshot, sig.boxes)
            event["duration_s"] = 0.0
            self._incidents[key] = Incident(event, now, now, now)
            self.events_created += 1
            return self._message(event, CREATED)

        # Ongoing incident: update the same event instead of creating new ones.
        inc.last_seen = now
        ev = inc.event
        ev["confidence"] = round(max(ev["confidence"], sig.confidence), 3)
        if sig.count is not None:
            ev["count"] = max(ev.get("count") or 0, sig.count)
            ev["message"] = sig.message
        ev["duration_s"] = round(now - inc.started, 1)
        if now - inc.last_published >= self.update_every:
            inc.last_published = now
            return self._message(ev, UPDATED)
        return None

    # ---------------------------------------------------------------- helpers
    def _build(self, cam, cam_name, sig: Signal, det_ts, disaster, status) -> dict:
        return {
            "event_id": new_event_id(det_ts),
            "camera_id": cam,
            "camera_name": cam_name,
            "event_type": sig.event_type,
            "severity": severity_for(sig.event_type, disaster),
            "confidence": round(float(sig.confidence), 3),
            "tracking_id": sig.tracking_id,
            "zone": sig.zone_name or sig.zone_id,
            "count": sig.count,
            "message": sig.message,
            "status": status,
            "mode": "disaster" if disaster else "security",
            "timestamp": _iso(det_ts),
            "snapshot_path": None,
            "detection_ts": round(det_ts * 1000, 3),  # epoch ms, used for latency
        }

    def _message(self, event: dict, action: str) -> dict:
        msg = dict(event)
        msg["action"] = action
        return msg

    def _save_snapshot(self, event: dict, snapshot: Callable[[list], np.ndarray], boxes) -> str | None:
        try:
            img = snapshot(boxes)
            day = event["timestamp"][:10]
            folder = self.snapshot_dir / day
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{event['event_id']}.jpg"
            cv2.imwrite(str(path), img, [cv2.IMWRITE_JPEG_QUALITY, 85])
            return f"snapshots/{day}/{path.name}"  # also its URL path on the web server
        except Exception as exc:  # a failed snapshot must never lose the event
            log.error("Snapshot failed for %s: %s", event["event_id"], exc)
            return None

    def _prune(self, now: float):
        for key, t in list(self._emitted.items()):
            if now - t > 600:
                del self._emitted[key]


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts).isoformat(timespec="milliseconds")
