"""GET /stores/<store_id>/funnel — conversion funnel with drop-off %."""

import time
from flask import Blueprint, jsonify
from database import get_db
from models import FunnelResponse, FunnelStage

funnel_bp = Blueprint("funnel", __name__)


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _dropoff(prev: int, curr: int) -> float:
    if prev == 0:
        return 0.0
    return round((prev - curr) / prev * 100, 1)


@funnel_bp.route("/stores/<store_id>/funnel")
def get_funnel(store_id: str):
    today = _today()
    try:
        with get_db() as db:
            exists = db.execute(
                "SELECT 1 FROM events WHERE store_id=? LIMIT 1", (store_id,)
            ).fetchone()
            if not exists:
                return jsonify({"error": "store_not_found", "store_id": store_id}), 404

            # Stage 1 — Entry: unique visitors with ENTRY today
            s1 = db.execute("""
                SELECT COUNT(DISTINCT visitor_id) FROM events
                WHERE store_id=? AND is_staff=0 AND visitor_id IS NOT NULL
                  AND event_type='ENTRY' AND date(timestamp)=?
            """, (store_id, today)).fetchone()[0] or 0

            # Stage 2 — Zone visit: unique visitors with any ZONE_ENTER
            s2 = db.execute("""
                SELECT COUNT(DISTINCT visitor_id) FROM events
                WHERE store_id=? AND is_staff=0 AND visitor_id IS NOT NULL
                  AND event_type='ZONE_ENTER' AND date(timestamp)=?
            """, (store_id, today)).fetchone()[0] or 0

            # Stage 3 — Billing queue: reached billing zone
            s3 = db.execute("""
                SELECT COUNT(DISTINCT visitor_id) FROM events
                WHERE store_id=? AND is_staff=0 AND visitor_id IS NOT NULL
                  AND event_type IN ('BILLING_QUEUE_JOIN','ZONE_ENTER')
                  AND zone_id IN (
                      SELECT DISTINCT zone_id FROM events
                      WHERE store_id=? AND event_type='BILLING_QUEUE_JOIN'
                  )
                  AND date(timestamp)=?
            """, (store_id, store_id, today)).fetchone()[0] or 0

            # Stage 4 — Purchase: converted=1
            s4 = db.execute("""
                SELECT COUNT(DISTINCT visitor_id) FROM events
                WHERE store_id=? AND is_staff=0 AND visitor_id IS NOT NULL
                  AND converted=1 AND date(timestamp)=?
            """, (store_id, today)).fetchone()[0] or 0

    except Exception as e:
        return jsonify({"error": "database_unavailable", "detail": str(e), "retry_after": 30}), 503

    stages = [
        FunnelStage(stage="entry",         count=s1, dropoff_pct=0.0),
        FunnelStage(stage="zone_visit",     count=s2, dropoff_pct=_dropoff(s1, s2)),
        FunnelStage(stage="billing_queue",  count=s3, dropoff_pct=_dropoff(s2, s3)),
        FunnelStage(stage="purchase",       count=s4, dropoff_pct=_dropoff(s3, s4)),
    ]
    resp = FunnelResponse(store_id=store_id, date=today, stages=stages)
    return jsonify(resp.model_dump())
