"""Camera configuration, live status and restricted zones."""
from __future__ import annotations

from typing import List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..context import AppContext, get_ctx

router = APIRouter(prefix="/api/cameras", tags=["cameras"])


class CameraUpdate(BaseModel):
    name: Optional[str] = None
    type: Optional[str] = None
    source: Optional[Union[str, int]] = None
    enabled: Optional[bool] = None


class Zone(BaseModel):
    id: str
    name: str = "Restricted Area"
    points: List[List[float]] = Field(..., min_length=3)
    intrusion: bool = True
    loitering: bool = True
    classes: List[str] = ["person"]


def _engine(ctx: AppContext):
    if ctx.engine is None:
        raise HTTPException(503, "AI engine is disabled (ENABLE_ANALYTICS=false)")
    return ctx.engine


@router.get("")
def list_cameras(ctx: AppContext = Depends(get_ctx)):
    if ctx.engine is None:
        return ctx.db.cameras()
    return ctx.engine.cameras()


@router.put("/{camera_id}")
def update_camera(camera_id: str, body: CameraUpdate, ctx: AppContext = Depends(get_ctx)):
    engine = _engine(ctx)
    changes = body.model_dump(exclude_none=True)
    if changes.get("type") == "webcam" and "source" in changes:
        try:
            changes["source"] = int(changes["source"])
        except ValueError:
            raise HTTPException(400, "Webcam source must be a device index such as 0 or 1")
    try:
        info = engine.update_camera(camera_id, changes)
    except KeyError:
        raise HTTPException(404, f"Unknown camera {camera_id}")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    ctx.db.upsert_camera(info)
    return info


@router.get("/{camera_id}/zones")
def get_zones(camera_id: str, ctx: AppContext = Depends(get_ctx)):
    return _engine(ctx).zones.get(camera_id, [])


@router.put("/{camera_id}/zones")
def set_zones(camera_id: str, zones: List[Zone], ctx: AppContext = Depends(get_ctx)):
    engine = _engine(ctx)
    if camera_id not in engine.sources:
        raise HTTPException(404, f"Unknown camera {camera_id}")
    for z in zones:
        if any(not (0 <= v <= 1) for p in z.points for v in p):
            raise HTTPException(400, "Zone points must be normalised between 0 and 1")
    engine.set_zones(camera_id, [z.model_dump() for z in zones])
    return engine.zones[camera_id]
