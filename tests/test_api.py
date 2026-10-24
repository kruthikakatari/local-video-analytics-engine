"""FastAPI backend: REST endpoints + WebSocket delivery + latency ack (AI engine disabled)."""
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from config.settings import settings
    monkeypatch.setattr(settings, "enable_analytics", False)
    monkeypatch.setattr(settings, "database_path", tmp_path / "api.db")
    monkeypatch.setattr(settings, "snapshot_dir", tmp_path / "snaps")
    monkeypatch.setattr(settings, "mqtt_port", 1)   # no broker: the app must still work
    from backend.main import app
    with TestClient(app) as c:
        yield c


def event(eid):
    now = time.time() * 1000
    return {"event_id": eid, "camera_id": "camera_01", "camera_name": "Webcam",
            "event_type": "INTRUSION", "severity": "high", "confidence": 0.91, "tracking_id": 12,
            "timestamp": "2026-09-28T20:32:10.000", "status": "LOGGED", "action": "created",
            "snapshot_path": None, "detection_ts": now, "publish_ts": None}


def test_status_reports_components(client):
    s = client.get("/api/system/status").json()
    assert s["backend"]["status"] == "online"
    assert s["database"]["status"] == "online"
    assert s["mqtt"]["status"] == "offline"          # broker unreachable, app still running
    assert s["ai_engine"]["status"] == "disabled"


def test_event_flows_to_websocket_db_and_metrics(client):
    ctx = client.app.state.ctx
    with client.websocket_connect("/ws") as ws:
        assert ws.receive_json()["type"] == "hello"
        ctx.events.handle_fallback(event("evt_test_1"))       # as if delivered by the AI engine
        msg = ws.receive_json()
        assert msg["type"] == "event" and msg["event"]["id"] == "evt_test_1"
        ws.send_json({"type": "ack", "event_id": "evt_test_1", "received_at": time.time() * 1000})
        lat = ws.receive_json()
        assert lat["type"] == "latency" and lat["latency_ms"] >= 0

        ws.send_json({"type": "sync", "t0": 123})
        assert ws.receive_json()["t0"] == 123

    data = client.get("/api/events").json()
    assert data["total"] == 1 and data["events"][0]["tracking_id"] == 12
    assert client.get("/api/events?event_type=FIRE_DETECTED").json()["total"] == 0
    detail = client.get("/api/events/evt_test_1").json()
    assert detail["metrics"]["latency_ms"] is not None
    assert client.get("/api/events/nope").status_code == 404

    m = client.get("/api/metrics").json()
    assert m["events_processed"] == 1 and m["latency"]["end_to_end_ms"]["count"] == 1
    assert "evt_test_1" in client.get("/api/metrics/export.csv").text


def test_dashboard_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "LOCAL VIDEO ANALYTICS" in r.text
