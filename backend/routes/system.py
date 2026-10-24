"""System status (for the dashboard's status bar) and Disaster Mode."""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..context import AppContext, get_ctx, lan_addresses

router = APIRouter(prefix="/api/system", tags=["system"])


class DisasterMode(BaseModel):
    enabled: bool


@router.get("/status")
def system_status(ctx: AppContext = Depends(get_ctx)):
    s, engine, events = ctx.settings, ctx.engine, ctx.events
    eng = engine.status() if engine else None
    heartbeat = events.system_status.get("received_at")
    ai_status = "disabled"
    if eng:
        ai_status = "error" if eng["detector"] == "error" else (
            "loading" if eng["detector"] == "loading" else "running")

    return {
        "backend": {"status": "online", "uptime_s": round(time.time() - events.started_at)},
        "mqtt": {
            "status": "online" if events.mqtt.connected else "offline",
            "broker": f"{s.mqtt_host}:{s.mqtt_port}",
            "backend_subscriber": events.mqtt.connected,
            "engine_publisher": bool(eng and eng["mqtt_connected"]),
            "error": events.mqtt.last_error,
            "last_heartbeat_s": round(time.time() - heartbeat, 1) if heartbeat else None,
        },
        "database": {"status": "online" if ctx.db.ping() else "offline",
                     "path": str(s.database_path.name)},
        "ai_engine": {"status": ai_status,
                      "detector": eng["detector_message"] if eng else "ENABLE_ANALYTICS=false",
                      "events_created": eng["events_created"] if eng else 0},
        "fire_model": {"status": eng["fire_model"] if eng else "not_configured",
                       "message": eng["fire_message"] if eng else ""},
        "cameras": [{"id": c["id"], "name": c["name"], "status": c["status"],
                     "error": c.get("error"), "demo_mode": c.get("demo_mode", False)}
                    for c in (engine.cameras() if engine else ctx.db.cameras())],
        "disaster_mode": bool(engine and engine.disaster_mode),
        "network": {"lan_addresses": lan_addresses(), "port": s.port},
        "config": {"process_fps": s.process_fps, "confidence_threshold": s.confidence_threshold,
                   "crowd_threshold": s.crowd_threshold, "loitering_seconds": s.loitering_seconds,
                   "event_cooldown_seconds": s.event_cooldown_seconds,
                   "rtsp_transport": s.rtsp_transport, "mqtt_qos": s.mqtt_qos},
    }


@router.post("/disaster-mode")
def set_disaster_mode(body: DisasterMode, ctx: AppContext = Depends(get_ctx)):
    if ctx.engine is None:
        raise HTTPException(503, "AI engine is disabled")
    ctx.engine.set_disaster_mode(body.enabled)
    return {"disaster_mode": ctx.engine.disaster_mode}
