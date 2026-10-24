"""Zone intrusion, loitering, crowd and person rules."""
from analytics.rule_engine import (CROWD_ALERT, INTRUSION, LOITERING, PERSON_DETECTED, RuleEngine,
                                   point_in_zone)
from analytics.tracker import TrackedObject

W, H = 1000, 500
ZONE = {"id": "z1", "name": "Server Room", "points": [[0.5, 0.0], [1.0, 0.0], [1.0, 1.0], [0.5, 1.0]],
        "intrusion": True, "loitering": True, "classes": ["person"]}


def trk(tid, x, cls="person", conf=0.9):
    return TrackedObject(tid, (x, 100, x + 40, 300), conf, cls)


def types(result):
    return [s.event_type for s in result.oneshots]


def test_point_in_zone():
    assert point_in_zone((800, 250), ZONE, W, H)
    assert not point_in_zone((200, 250), ZONE, W, H)


def test_intrusion_only_on_entry():
    r = RuleEngine(loitering_seconds=100)
    assert types(r.evaluate([trk(1, 100)], W, H, [ZONE], 0.0)) == []
    res = r.evaluate([trk(1, 700)], W, H, [ZONE], 1.0)          # feet enter the zone
    assert types(res) == [INTRUSION]
    assert res.oneshots[0].tracking_id == 1 and res.oneshots[0].zone_name == "Server Room"
    assert types(r.evaluate([trk(1, 710)], W, H, [ZONE], 1.2)) == []  # still inside: no repeat


def test_vehicle_ignored_by_person_zone():
    r = RuleEngine()
    assert types(r.evaluate([trk(5, 700, "car")], W, H, [ZONE], 0.0)) == []


def test_loitering_after_threshold():
    r = RuleEngine(loitering_seconds=10)
    t = 0.0
    seen = []
    while t <= 12:
        seen += types(r.evaluate([trk(3, 700)], W, H, [ZONE], t))
        t += 0.5
    assert INTRUSION in seen and LOITERING in seen


def test_leaving_zone_resets_loitering_timer():
    r = RuleEngine(loitering_seconds=5, zone_exit_grace_seconds=1)
    r.evaluate([trk(3, 700)], W, H, [ZONE], 0)
    r.evaluate([trk(3, 100)], W, H, [ZONE], 4)                  # walks out
    r.evaluate([trk(3, 100)], W, H, [ZONE], 6)
    res = r.evaluate([trk(3, 700)], W, H, [ZONE], 7)            # re-enters
    assert INTRUSION in types(res)
    assert LOITERING not in types(r.evaluate([trk(3, 700)], W, H, [ZONE], 9))


def test_crowd_condition():
    r = RuleEngine(crowd_threshold=2)
    res = r.evaluate([trk(i, i * 50) for i in range(1, 3)], W, H, [], 0)
    assert res.conditions[CROWD_ALERT] is None and res.people_count == 2
    res = r.evaluate([trk(i, i * 50) for i in range(1, 4)], W, H, [], 1)
    assert res.conditions[CROWD_ALERT].count == 3


def test_person_detected_only_when_enabled():
    r = RuleEngine()
    assert types(r.evaluate([trk(1, 100)], W, H, [], 0, person_events=False)) == []
    r2 = RuleEngine()
    assert types(r2.evaluate([trk(1, 100)], W, H, [], 0, person_events=True)) == [PERSON_DETECTED]
    assert types(r2.evaluate([trk(1, 105)], W, H, [], 1, person_events=True)) == []
