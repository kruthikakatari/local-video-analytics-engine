"""
Live video for the dashboard as MJPEG over HTTP
(multipart/x-mixed-replace: the browser shows each JPEG as it arrives).

Video path:  camera --RTSP/USB--> laptop --HTTP MJPEG--> browser
Event path:  AI engine --MQTT--> backend --WebSocket--> browser
"""
from __future__ import annotations

import asyncio
import time

import cv2
import numpy as np
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from starlette.concurrency import run_in_threadpool

from ..context import AppContext, get_ctx

router = APIRouter(tags=["video"])
BOUNDARY = "frame"


FRIENDLY = {"OFFLINE": "Not connected", "CONNECTION LOST": "Connection lost",
            "CONNECTING": "Connecting...", "DISABLED": "Turned off"}


def placeholder(camera_name: str, status: str, detail: str = "No picture right now") -> bytes:
    """Image shown instead of video while a camera has no picture."""
    text = FRIENDLY.get(status, status)
    img = np.full((360, 640, 3), (28, 22, 18), np.uint8)
    cv2.putText(img, camera_name, (30, 150), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (160, 160, 160), 2,
                cv2.LINE_AA)
    cv2.putText(img, text, (30, 195), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (80, 80, 240), 2, cv2.LINE_AA)
    if detail:
        cv2.putText(img, detail[:60], (30, 235), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (150, 150, 150), 1,
                    cv2.LINE_AA)
    return cv2.imencode(".jpg", img)[1].tobytes()


@router.get("/video/{camera_id}")
async def video_stream(camera_id: str, request: Request, ctx: AppContext = Depends(get_ctx)):
    engine = ctx.engine
    if engine is None or camera_id not in engine.sources:
        raise HTTPException(404, "Unknown camera")
    interval = 1.0 / max(1.0, ctx.settings.stream_fps)

    async def frames():
        ctx.video["active"] += 1
        try:
            while not await request.is_disconnected():
                started = time.time()
                src = engine.sources.get(camera_id)
                if src is None:
                    break
                data = None
                if src.status == "ONLINE":
                    data = await run_in_threadpool(engine.jpeg, camera_id) or \
                        await run_in_threadpool(engine.raw_jpeg, camera_id)
                if data is None:
                    data = placeholder(src.name, src.status)
                    wait = 1.0
                else:
                    wait = interval
                ctx.video["bytes"] += len(data)
                ctx.video["frames"] += 1
                yield (f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\n"
                       f"Content-Length: {len(data)}\r\n\r\n").encode() + data + b"\r\n"
                await asyncio.sleep(max(0.0, wait - (time.time() - started)))
        finally:
            ctx.video["active"] -= 1

    return StreamingResponse(frames(), media_type=f"multipart/x-mixed-replace; boundary={BOUNDARY}",
                             headers={"Cache-Control": "no-store"})


@router.get("/video/{camera_id}/frame.jpg")
async def video_frame(camera_id: str, raw: bool = False, ctx: AppContext = Depends(get_ctx)):
    """Single frame (raw=true: without overlay, used by the zone editor)."""
    engine = ctx.engine
    if engine is None or camera_id not in engine.sources:
        raise HTTPException(404, "Unknown camera")
    fn = engine.raw_jpeg if raw else engine.jpeg
    data = await run_in_threadpool(fn, camera_id) or await run_in_threadpool(engine.raw_jpeg, camera_id)
    if data is None:
        src = engine.sources[camera_id]
        data = placeholder(src.name, src.status)
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
