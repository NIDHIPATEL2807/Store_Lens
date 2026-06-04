"""GET /stores/{store_id}/funnel — conversion funnel with drop-off %."""

import time
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from database import get_db
from models import FunnelResponse, FunnelStage

router = APIRouter()


def _dropoff(prev: int, curr: int) -> float:
    if prev == 0:
        return 0.0
    return round((prev - curr) / prev * 100, 1)


@router.get("/stores/{store_id}/funnel")
def get_funnel(store_id: str, date: Optional[str] = None):
    try:
        with get_db() as db:
            exists = db.execute(
                "SELECT 1 FROM events WHERE store_id=? LIMIT 1", (store_id,)
            ).fetchone()
            if not exists:
                return JSONResponse({"error": "store_not_found", "store_id": store_id}, status_code=404)

            if date is None:
                date = db.execute(
                    "SELECT date(MAX(timestamp)) FROM events WHERE store_id=?", (store_id,)
                ).fetchone()[0] or time.strftime("%Y-%m-%d", time.gmtime())

            s1 = db.execute("""
                SELECT COUNT(DISTINCT visitor_id) FROM events
                WHERE store_id=? AND is_staff=0 AND visitor_id IS NOT NULL
                  AND event_type='ENTRY' AND date(timestamp)=?
            """, (store_id, date)).fetchone()[0] or 0

            s2 = db.execute("""
                SELECT COUNT(DISTINCT visitor_id) FROM events
                WHERE store_id=? AND is_staff=0 AND visitor_id IS NOT NULL
                  AND event_type='ZONE_ENTER' AND date(timestamp)=?
            """, (store_id, date)).fetchone()[0] or 0

            s3 = db.execute("""
                SELECT COUNT(DISTINCT visitor_id) FROM events
                WHERE store_id=? AND is_staff=0 AND visitor_id IS NOT NULL
                  AND event_type IN ('BILLING_QUEUE_JOIN','ZONE_ENTER')
                  AND zone_id IN (
                      SELECT DISTINCT zone_id FROM events
                      WHERE store_id=? AND event_type='BILLING_QUEUE_JOIN'
                  )
                  AND date(timestamp)=?
            """, (store_id, store_id, date)).fetchone()[0] or 0

            s4 = db.execute("""
                SELECT COUNT(DISTINCT visitor_id) FROM events
                WHERE store_id=? AND is_staff=0 AND visitor_id IS NOT NULL
                  AND converted=1 AND date(timestamp)=?
            """, (store_id, date)).fetchone()[0] or 0

    except Exception as e:
        return JSONResponse(
            {"error": "database_unavailable", "detail": str(e), "retry_after": 30},
            status_code=503,
        )

    stages = [
        FunnelStage(stage="entry",        count=s1, dropoff_pct=0.0),
        FunnelStage(stage="zone_visit",    count=s2, dropoff_pct=_dropoff(s1, s2)),
        FunnelStage(stage="billing_queue", count=s3, dropoff_pct=_dropoff(s2, s3)),
        FunnelStage(stage="purchase",      count=s4, dropoff_pct=_dropoff(s3, s4)),
    ]
    return FunnelResponse(store_id=store_id, date=date, stages=stages).model_dump()
