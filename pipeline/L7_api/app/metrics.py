"""GET /stores/<store_id>/metrics — live store metrics."""

from flask import Blueprint, jsonify
from database import get_db
from models import MetricsResponse, ZoneDwell
import time

metrics_bp = Blueprint("metrics", __name__)


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


@metrics_bp.route("/stores/<store_id>/metrics")
def get_metrics(store_id: str):
    today = _today()
    try:
        with get_db() as db:
            # Verify store exists
            exists = db.execute(
                "SELECT 1 FROM events WHERE store_id=? LIMIT 1", (store_id,)
            ).fetchone()
            if not exists:
                return jsonify({"error": "store_not_found", "store_id": store_id}), 404

            # Unique visitors today
            uv = db.execute("""
                SELECT COUNT(DISTINCT visitor_id) FROM events
                WHERE store_id=? AND is_staff=0 AND visitor_id IS NOT NULL
                  AND event_type='ENTRY' AND date(timestamp)=?
            """, (store_id, today)).fetchone()[0] or 0

            # Conversion rate — visitors who reached billing zone / total unique visitors
            billing_visitors = db.execute("""
                SELECT COUNT(DISTINCT visitor_id) FROM events
                WHERE store_id=? AND is_staff=0 AND visitor_id IS NOT NULL
                  AND event_type IN ('BILLING_QUEUE_JOIN','ZONE_ENTER')
                  AND zone_id IS NOT NULL
                  AND date(timestamp)=?
                  AND zone_id IN (
                      SELECT DISTINCT zone_id FROM events
                      WHERE store_id=? AND event_type='BILLING_QUEUE_JOIN'
                  )
            """, (store_id, today, store_id)).fetchone()[0] or 0
            conversion_rate = round(billing_visitors / uv, 4) if uv > 0 else 0.0

            # Avg dwell per zone
            dwell_rows = db.execute("""
                SELECT zone_id, AVG(dwell_ms) FROM events
                WHERE store_id=? AND is_staff=0
                  AND event_type IN ('ZONE_EXIT','ZONE_DWELL')
                  AND zone_id IS NOT NULL AND date(timestamp)=?
                GROUP BY zone_id ORDER BY AVG(dwell_ms) DESC
            """, (store_id, today)).fetchall()
            avg_dwell = [ZoneDwell(zone_id=r[0], avg_dwell_ms=round(r[1], 1))
                         for r in dwell_rows]

            # Current queue depth — in billing zone without exit
            queue_depth = db.execute("""
                SELECT COUNT(DISTINCT visitor_id) FROM events e1
                WHERE store_id=? AND is_staff=0
                  AND event_type IN ('BILLING_QUEUE_JOIN','ZONE_ENTER')
                  AND zone_id IN (
                      SELECT DISTINCT zone_id FROM events
                      WHERE store_id=? AND event_type='BILLING_QUEUE_JOIN'
                  )
                  AND NOT EXISTS (
                      SELECT 1 FROM events e2
                      WHERE e2.visitor_id=e1.visitor_id
                        AND e2.store_id=e1.store_id
                        AND e2.event_type='ZONE_EXIT'
                        AND e2.zone_id=e1.zone_id
                        AND e2.timestamp > e1.timestamp
                  )
            """, (store_id, store_id)).fetchone()[0] or 0

            # Abandonment rate
            joins = db.execute("""
                SELECT COUNT(*) FROM events
                WHERE store_id=? AND event_type='BILLING_QUEUE_JOIN'
                  AND date(timestamp)=?
            """, (store_id, today)).fetchone()[0] or 0
            abandons = db.execute("""
                SELECT COUNT(*) FROM events
                WHERE store_id=? AND event_type='BILLING_QUEUE_ABANDON'
                  AND date(timestamp)=?
            """, (store_id, today)).fetchone()[0] or 0
            abandonment_rate = round(abandons / joins, 4) if joins > 0 else 0.0

    except Exception as e:
        return jsonify({"error": "database_unavailable", "detail": str(e), "retry_after": 30}), 503

    resp = MetricsResponse(
        store_id=store_id, date=today,
        unique_visitors=uv,
        conversion_rate=conversion_rate,
        avg_dwell_by_zone=avg_dwell,
        current_queue_depth=queue_depth,
        abandonment_rate=abandonment_rate,
    )
    return jsonify(resp.model_dump())
