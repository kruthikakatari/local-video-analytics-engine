"""Event history (SQLite)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from ..context import AppContext, get_ctx

router = APIRouter(prefix="/api/events", tags=["events"])


@router.get("")
def list_events(camera_id: Optional[str] = None, event_type: Optional[str] = None,
                severity: Optional[str] = None, date: Optional[str] = Query(None, description="YYYY-MM-DD"),
                status: Optional[str] = None, q: Optional[str] = None,
                limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0),
                ctx: AppContext = Depends(get_ctx)):
    rows, total = ctx.db.list_events(camera_id, event_type, severity, date, status, q, limit, offset)
    return {"total": total, "limit": limit, "offset": offset, "events": rows}


@router.get("/active")
def active_events(ctx: AppContext = Depends(get_ctx)):
    return ctx.db.active_events()


@router.get("/{event_id}")
def get_event(event_id: str, ctx: AppContext = Depends(get_ctx)):
    ev = ctx.db.get_event(event_id)
    if not ev:
        raise HTTPException(404, "Event not found")
    return ev
