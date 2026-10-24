"""
WebSocket connection manager: pushes events to every open dashboard instantly
(no page refresh, no polling).

Messages server -> dashboard
  {"type": "event", "action": "created|updated|resolved", "event": {...}}
  {"type": "camera_status", ...}   {"type": "system_status", ...}
  {"type": "latency", "event_id": ..., "latency_ms": ...}
  {"type": "sync", "t0": ..., "server_ts": ...}

Messages dashboard -> server
  {"type": "sync", "t0": <browser ms>}              clock synchronisation (NTP-style)
  {"type": "ack", "event_id": ..., "received_at": <server-clock ms>}

Why clock sync? The dashboard may run on a phone whose clock differs from the
laptop's. The browser estimates offset = server_ts - (t0 + t3) / 2 (like NTP)
and reports the receive time in the server's clock, so the end-to-end latency
(dashboard receive - detection) is measured correctly.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import WebSocket

log = logging.getLogger(__name__)


class WebSocketManager:
    def __init__(self):
        self.clients: set[WebSocket] = set()
        self.messages_sent = 0
        self.bytes_sent = 0

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.clients.add(ws)
        log.info("Dashboard connected via WebSocket (%d open)", len(self.clients))

    def disconnect(self, ws: WebSocket):
        self.clients.discard(ws)
        log.info("Dashboard disconnected (%d open)", len(self.clients))

    async def send(self, ws: WebSocket, message: dict):
        await ws.send_text(json.dumps(message, default=str))

    async def broadcast(self, message: dict) -> float:
        """Send to all dashboards. Returns the send timestamp (epoch ms)."""
        message["ws_ts"] = round(time.time() * 1000, 3)
        text = json.dumps(message, default=str)
        dead = []
        for ws in list(self.clients):
            try:
                await ws.send_text(text)
                self.messages_sent += 1
                self.bytes_sent += len(text)
            except Exception:  # closed tab, network drop...
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)
        return message["ws_ts"]

    def broadcast_threadsafe(self, loop: asyncio.AbstractEventLoop, message: dict,
                             on_sent=None):
        """Called from the MQTT thread: schedule the broadcast on the web server's event loop."""
        async def _run():
            ts = await self.broadcast(message)
            if on_sent:
                on_sent(ts)
        try:
            asyncio.run_coroutine_threadsafe(_run(), loop)
        except RuntimeError:  # loop closed during shutdown
            pass
