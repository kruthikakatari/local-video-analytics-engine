"""
Zone / rule engine.

Turns tracked objects into *signals* (candidate events). It does not decide
whether an alert is sent; that is the Event Manager's job (cooldown,
de-duplication, incidents).

Rules
-----
* INTRUSION        a tracked person's feet enter a restricted polygon zone
* LOITERING        a tracked person stays inside a zone for > LOITERING_SECONDS
* CROWD_ALERT      people in view > CROWD_THRESHOLD           (condition)
* PERSON_DETECTED  a new person track appears (Disaster Mode: "person located")

Zones are stored with normalised coordinates (0..1), so they work at any
camera resolution.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from .object_detector import PERSON, VEHICLE_CLASSES
from .tracker import TrackedObject

INTRUSION = "INTRUSION"
LOITERING = "LOITERING"
CROWD_ALERT = "CROWD_ALERT"
PERSON_DETECTED = "PERSON_DETECTED"
FIRE_DETECTED = "FIRE_DETECTED"
SMOKE_DETECTED = "SMOKE_DETECTED"

EVENT_TYPES = [PERSON_DETECTED, INTRUSION, LOITERING, CROWD_ALERT, FIRE_DETECTED, SMOKE_DETECTED]


@dataclass
class Signal:
    event_type: str
    confidence: float
    tracking_id: int | None = None
    zone_id: str | None = None
    zone_name: str | None = None
    count: int | None = None
    message: str = ""
    boxes: list = field(default_factory=list)  # boxes to highlight in the snapshot


@dataclass
class RuleResult:
    oneshots: list[Signal]           # fire once per track / zone
    conditions: dict[str, Signal | None]  # ongoing states (crowd); None = not active
    people_count: int
    vehicle_count: int
    tracks_in_zone: set[int]


def foot_point(box) -> tuple[float, float]:
    """Bottom-centre of the box = where the person stands on the ground."""
    return (box[0] + box[2]) / 2.0, box[3]


def zone_polygon_px(zone: dict, width: int, height: int) -> np.ndarray:
    return np.array([[x * width, y * height] for x, y in zone["points"]], dtype=np.float32)


def point_in_zone(point, zone: dict, width: int, height: int) -> bool:
    poly = zone_polygon_px(zone, width, height)
    if len(poly) < 3:
        return False
    return cv2.pointPolygonTest(poly, (float(point[0]), float(point[1])), False) >= 0


class RuleEngine:
    """One instance per camera (it remembers who is in which zone and since when)."""

    def __init__(self, crowd_threshold: int = 5, loitering_seconds: float = 10.0,
                 zone_exit_grace_seconds: float = 2.0):
        self.crowd_threshold = crowd_threshold
        self.loitering_seconds = loitering_seconds
        self.zone_exit_grace = zone_exit_grace_seconds
        self._zone_entry: dict[tuple[int, str], float] = {}   # (track, zone) -> entered at
        self._zone_last: dict[tuple[int, str], float] = {}    # (track, zone) -> last seen inside
        self._seen_people: dict[int, float] = {}              # track -> last seen

    def evaluate(self, tracks: list[TrackedObject], width: int, height: int,
                 zones: list[dict], now: float, person_events: bool = False) -> RuleResult:
        oneshots: list[Signal] = []
        people = [t for t in tracks if t.class_name == PERSON]
        vehicles = [t for t in tracks if t.class_name in VEHICLE_CLASSES]
        in_zone: set[int] = set()

        # ---- PERSON_DETECTED: first time a person track is confirmed
        for p in people:
            if p.track_id not in self._seen_people and person_events:
                oneshots.append(Signal(PERSON_DETECTED, p.confidence, p.track_id,
                                       message=f"Person #{p.track_id} located",
                                       boxes=[p.box]))
            self._seen_people[p.track_id] = now

        # ---- Zone rules
        for zone in zones:
            classes = set(zone.get("classes") or [PERSON])
            for t in tracks:
                if t.class_name not in classes:
                    continue
                if not point_in_zone(foot_point(t.box), zone, width, height):
                    continue
                key = (t.track_id, zone["id"])
                in_zone.add(t.track_id)
                label = f"{t.class_name.capitalize()} #{t.track_id}"
                if key not in self._zone_entry:
                    self._zone_entry[key] = now
                    if zone.get("intrusion", True):
                        oneshots.append(Signal(
                            INTRUSION, t.confidence, t.track_id, zone["id"], zone.get("name"),
                            message=f"{label} entered {zone.get('name', zone['id'])}",
                            boxes=[t.box]))
                self._zone_last[key] = now
                dwell = now - self._zone_entry[key]
                if zone.get("loitering", True) and dwell >= self.loitering_seconds:
                    oneshots.append(Signal(
                        LOITERING, t.confidence, t.track_id, zone["id"], zone.get("name"),
                        message=f"{label} in {zone.get('name', zone['id'])} for {dwell:.0f}s",
                        boxes=[t.box]))

        # ---- CROWD_ALERT (a condition, handled as an incident)
        crowd = None
        if len(people) > self.crowd_threshold:
            crowd = Signal(CROWD_ALERT, float(np.mean([p.confidence for p in people])),
                           count=len(people),
                           message=f"{len(people)} people in view (limit {self.crowd_threshold})",
                           boxes=[p.box for p in people])

        self._forget(now)
        return RuleResult(oneshots, {CROWD_ALERT: crowd}, len(people), len(vehicles), in_zone)

    def _forget(self, now: float):
        """A track that left the zone (or vanished) must re-enter to trigger again."""
        for key, last in list(self._zone_last.items()):
            if now - last > self.zone_exit_grace:  # left the zone (short tracker gaps tolerated)
                self._zone_last.pop(key, None)
                self._zone_entry.pop(key, None)
        for tid, last in list(self._seen_people.items()):
            if now - last > 300:
                self._seen_people.pop(tid, None)
