"""Event Manager: de-duplication, cooldown, fire incidents, snapshots, compact messages."""
import json

import numpy as np

from analytics.event_manager import EventManager
from analytics.object_detector import Detection
from analytics.rule_engine import (CROWD_ALERT, FIRE_DETECTED, INTRUSION, RuleResult, Signal,
                                   SMOKE_DETECTED)


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def make(tmp_path, **kw):
    sent, clock = [], Clock()
    em = EventManager(sent.append, tmp_path, clock=clock, **kw)
    return em, sent, clock


FRAME = np.zeros((120, 160, 3), np.uint8)
snap = lambda boxes: FRAME  # noqa: E731


def rules(oneshots=(), crowd=None):
    return RuleResult(list(oneshots), {CROWD_ALERT: crowd}, 0, 0, set())


def intrusion(tid, zone="z1"):
    return Signal(INTRUSION, 0.9, tid, zone, "Zone 1", message="entered")


def test_intrusion_event_fields_and_snapshot(tmp_path):
    em, sent, clock = make(tmp_path)
    em.handle("camera_01", "Webcam", rules([intrusion(12)]), None, snap, clock.t)
    assert len(sent) == 1
    ev = sent[0]
    for key in ("event_id", "camera_id", "event_type", "confidence", "tracking_id", "timestamp",
                "snapshot_path", "severity", "detection_ts", "action"):
        assert key in ev
    assert ev["event_type"] == INTRUSION and ev["tracking_id"] == 12 and ev["action"] == "created"
    assert (tmp_path / ev["snapshot_path"].split("/", 1)[1]).exists()
    # compact JSON, no image data inside the message
    assert len(json.dumps(ev)) < 800


def test_same_track_never_repeats_and_cooldown(tmp_path):
    em, sent, clock = make(tmp_path, cooldown_seconds=5)
    em.handle("c", "c", rules([intrusion(1)]), None, snap, clock.t)
    clock.t += 1
    em.handle("c", "c", rules([intrusion(1)]), None, snap, clock.t)   # same person
    em.handle("c", "c", rules([intrusion(2)]), None, snap, clock.t)   # within cooldown
    assert len(sent) == 1
    clock.t += 6
    em.handle("c", "c", rules([intrusion(2)]), None, snap, clock.t)
    assert len(sent) == 2


def fire(conf=0.8):
    return [Detection((10, 10, 50, 50), conf, "fire")]


def test_continuous_fire_is_one_incident(tmp_path):
    em, sent, clock = make(tmp_path, fire_confirm_frames=2, incident_update_seconds=5,
                           incident_clear_seconds=10)
    for i in range(50):                      # 10 s of fire at 5 fps
        em.handle("c", "c", rules(), fire(0.6 + i * 0.005), snap, clock.t)
        clock.t += 0.2
    created = [m for m in sent if m["event_type"] == FIRE_DETECTED and m["action"] == "created"]
    updated = [m for m in sent if m["action"] == "updated"]
    assert len(created) == 1
    assert 1 <= len(updated) <= 2
    assert updated[-1]["event_id"] == created[0]["event_id"]
    assert updated[-1]["confidence"] > created[0]["confidence"]   # max confidence tracked

    for _ in range(60):                      # fire gone for 12 s
        em.handle("c", "c", rules(), [], snap, clock.t)
        clock.t += 0.2
    resolved = [m for m in sent if m["action"] == "resolved"]
    assert len(resolved) == 1 and resolved[0]["status"] == "RESOLVED"
    assert resolved[0]["confidence"] > 0.8 and resolved[0]["duration_s"] >= 9
    assert em.active_incidents() == []


def test_single_frame_fire_flicker_ignored(tmp_path):
    em, sent, clock = make(tmp_path, fire_confirm_frames=2)
    em.handle("c", "c", rules(), fire(), snap, clock.t)
    em.handle("c", "c", rules(), [], snap, clock.t + 0.2)
    em.handle("c", "c", rules(), fire(), snap, clock.t + 0.4)
    assert sent == []


def test_smoke_separate_from_fire(tmp_path):
    em, sent, clock = make(tmp_path, fire_confirm_frames=1)
    dets = [Detection((0, 0, 5, 5), 0.7, "smoke"), Detection((0, 0, 5, 5), 0.9, "fire")]
    em.handle("c", "c", rules(), dets, snap, clock.t)
    assert sorted(m["event_type"] for m in sent) == [FIRE_DETECTED, SMOKE_DETECTED]
    assert {m["severity"] for m in sent} == {"critical", "high"}


def test_crowd_incident(tmp_path):
    em, sent, clock = make(tmp_path)
    crowd = Signal(CROWD_ALERT, 0.8, count=7, message="7 people")
    for _ in range(10):
        em.handle("c", "c", rules(crowd=crowd), None, snap, clock.t)
        clock.t += 0.2
    assert [m["action"] for m in sent] == ["created"]
    assert sent[0]["count"] == 7


def test_snapshot_failure_does_not_lose_event(tmp_path):
    em, sent, clock = make(tmp_path)

    def broken(_):
        raise RuntimeError("disk full")
    em.handle("c", "c", rules([intrusion(1)]), None, broken, clock.t)
    assert len(sent) == 1 and sent[0]["snapshot_path"] is None
