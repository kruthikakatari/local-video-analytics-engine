"""SQLite: events, incident updates, duplicate delivery (QoS 1), filters, latency metrics."""
from backend.database import Database


def ev(eid="evt_1", **kw):
    base = {"event_id": eid, "camera_id": "camera_01", "camera_name": "Webcam",
            "event_type": "INTRUSION", "severity": "high", "confidence": 0.9, "tracking_id": 3,
            "timestamp": "2026-09-28T10:00:00.000", "status": "LOGGED", "action": "created",
            "snapshot_path": "snapshots/2026-09-28/evt_1.jpg"}
    base.update(kw)
    return base


def test_insert_query_and_duplicate(tmp_path):
    db = Database(tmp_path / "t.db")
    assert db.save_event(ev()) is True
    assert db.save_event(ev()) is False          # duplicate MQTT delivery ignored
    assert db.count_events() == 1
    got = db.get_event("evt_1")
    assert got["tracking_id"] == 3 and got["snapshot_path"].endswith("evt_1.jpg")


def test_incident_update_and_filters(tmp_path):
    db = Database(tmp_path / "t.db")
    db.save_event(ev("f1", event_type="FIRE_DETECTED", severity="critical", status="ACTIVE",
                     confidence=0.6, camera_id="camera_02"))
    db.save_event(ev("f1", event_type="FIRE_DETECTED", status="RESOLVED", confidence=0.95,
                     action="resolved", duration_s=12.0))
    db.save_event(ev("i2", timestamp="2026-09-29T09:00:00.000"))
    f = db.get_event("f1")
    assert f["status"] == "RESOLVED" and f["confidence"] == 0.95 and f["duration_s"] == 12.0
    rows, total = db.list_events(event_type="FIRE_DETECTED")
    assert total == 1 and rows[0]["id"] == "f1"
    assert db.list_events(camera_id="camera_02")[1] == 1
    assert db.list_events(date="2026-09-29")[1] == 1
    assert db.list_events(severity="critical")[1] == 1
    assert db.list_events(search="i2")[1] == 1


def test_latency_metrics(tmp_path):
    db = Database(tmp_path / "t.db")
    db.save_event(ev())
    db.save_metric("evt_1", detection_ts=1000.0, publish_ts=1003.0, receive_ts=1004.5,
                   ws_ts=None, payload_bytes=470, transport="mqtt")
    db.set_websocket_ts("evt_1", 1006.0)
    assert db.record_dashboard_receive("evt_1", 1008.0) == 8.0
    assert db.record_dashboard_receive("evt_1", 1020.0) is None   # first dashboard wins
    m = db.get_event("evt_1")["metrics"]
    assert m["mqtt_latency_ms"] == 1.5 and m["latency_ms"] == 8.0 and m["payload_bytes"] == 470
