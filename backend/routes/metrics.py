"""Network / performance metrics."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse

from ..context import AppContext, get_ctx

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


@router.get("")
def metrics(ctx: AppContext = Depends(get_ctx)):
    return ctx.metrics.summary()


@router.get("/export.csv", response_class=PlainTextResponse)
def export_csv(ctx: AppContext = Depends(get_ctx)):
    """Raw latency samples for the Results slide / report."""
    return PlainTextResponse(ctx.metrics.csv_export(), media_type="text/csv", headers={
        "Content-Disposition": "attachment; filename=latency_metrics.csv"})
