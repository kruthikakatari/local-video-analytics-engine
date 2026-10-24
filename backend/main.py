"""
FastAPI backend: REST API + WebSocket + live video + dashboard hosting.

Start with `python run.py` (recommended) or `uvicorn backend.main:app`.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from config.settings import BASE_DIR, settings

from .context import AppContext
from .database import Database
from .routes import cameras, events, metrics, system, video
from .services.event_service import EventService
from .services.metrics_service import MetricsService
from .websocket_manager import WebSocketManager

log = logging.getLogger("backend")
FRONTEND = BASE_DIR / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.snapshot_dir.mkdir(parents=True, exist_ok=True)
    db = Database(settings.database_path)
    ws = WebSocketManager()
    event_service = EventService(settings, db, ws)
    event_service.start(asyncio.get_running_loop())

    engine = None
    if settings.enable_analytics:
        from analytics.engine import AnalyticsEngine  # heavy imports only when needed
        engine = AnalyticsEngine(settings, fallback=event_service.handle_fallback)
        for cam in engine.store.load_cameras():
            db.upsert_camera({**cam, "status": "CONNECTING" if cam.get("enabled", True) else "DISABLED"})
        engine.start()

    ctx = AppContext(settings, db, ws, event_service, engine, None)
    ctx.metrics = MetricsService(db, event_service, ws, engine, ctx.video)
    app.state.ctx = ctx
    log.info("Backend ready")
    yield
    log.info("Shutting down...")
    if engine:
        engine.stop()
    event_service.stop()
    db.close()


app = FastAPI(title="Local Video Analytics & Security Engine",
              description="Offline edge surveillance and disaster management", lifespan=lifespan)

for r in (cameras.router, events.router, metrics.router, system.router, video.router):
    app.include_router(r)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    ctx: AppContext = websocket.app.state.ctx
    await ctx.ws.connect(websocket)
    try:
        await ctx.ws.send(websocket, {"type": "hello", "server_ts": time.time() * 1000,
                                      "active_events": ctx.db.active_events()})
        while True:
            try:
                msg = json.loads(await websocket.receive_text())
            except json.JSONDecodeError:
                continue
            kind = msg.get("type")
            if kind == "sync":  # NTP-style clock sync request
                await ctx.ws.send(websocket, {"type": "sync", "t0": msg.get("t0"),
                                              "server_ts": time.time() * 1000})
            elif kind == "ack" and msg.get("event_id") and msg.get("received_at"):
                latency = ctx.db.record_dashboard_receive(msg["event_id"], float(msg["received_at"]))
                if latency is not None:
                    await ctx.ws.broadcast({"type": "latency", "event_id": msg["event_id"],
                                            "latency_ms": round(latency, 2)})
    except WebSocketDisconnect:
        pass
    except Exception:  # never let one bad client affect the others
        log.exception("WebSocket error")
    finally:
        ctx.ws.disconnect(websocket)


# ---- Dashboard (static files) + snapshots
app.mount("/snapshots", StaticFiles(directory=str(settings.snapshot_dir), check_dir=False),
          name="snapshots")
app.mount("/static", StaticFiles(directory=str(FRONTEND)), name="static")


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(FRONTEND / "index.html")
