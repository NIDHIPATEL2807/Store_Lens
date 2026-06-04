"""GET /stores/{store_id}/anomalies — active anomalies with severity + action."""

import time
import uuid

from fastapi import APIRouter
from fastapi.responses import JSONResponse

import database
from models import AnomaliesResponse, Anomaly

router = APIRouter()

QUEUE_SPIKE_THRESHOLD    = 5
QUEUE_SPIKE_DURATION_MIN = 5
CONVERSION_WARN_RATIO    = 0.70
CONVERSION_CRIT_RATIO    = 0.50
DEAD_ZONE_MINUTES        = 30


def _now_utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


@router.get("/stores/{store_id}/anomalies")
def get_anomalies(store_id: str):
    now_str = _now_utc()
    today   = _today()
    detected: list[Anomaly] = []

    try:
        with database.get_db() as db:
            exists = db.execute(
                "SELECT 1 FROM events WHERE store_id=? LIMIT 1", (store_id,)
            ).fetchone()
            if not exists:
                return JSONResponse({"error": "store_not_found", "store_id": store_id}, status_code=404)

            # ── BILLING_QUEUE_SPIKE ───────────────────────────────────────────
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

            if queue_depth > QUEUE_SPIKE_THRESHOLD:
                spike_since = db.execute("""
                    SELECT MIN(timestamp) FROM events
                    WHERE store_id=? AND event_type='BILLING_QUEUE_JOIN'
                      AND timestamp >= datetime('now','-30 minutes')
                """, (store_id,)).fetchone()[0]
                if spike_since:
                    elapsed = (time.time() - time.mktime(
                        time.strptime(spike_since, "%Y-%m-%dT%H:%M:%S.%fZ")
                    )) / 60
                    if elapsed >= QUEUE_SPIKE_DURATION_MIN:
                        detected.append(Anomaly(
                            anomaly_id=str(uuid.uuid4()), store_id=store_id,
                            anomaly_type="BILLING_QUEUE_SPIKE", severity="WARN",
                            description=f"Queue depth is {queue_depth}, elevated for {int(elapsed)} min",
                            suggested_action="Open additional billing counter or call floor staff to assist.",
                            detected_at=now_str,
                        ))

            # ── CONVERSION_DROP ───────────────────────────────────────────────
            today_uv = db.execute("""
                SELECT COUNT(DISTINCT visitor_id) FROM events
                WHERE store_id=? AND is_staff=0 AND visitor_id IS NOT NULL
                  AND event_type='ENTRY' AND date(timestamp)=?
            """, (store_id, today)).fetchone()[0] or 0

            today_conv = db.execute("""
                SELECT COUNT(DISTINCT visitor_id) FROM events
                WHERE store_id=? AND is_staff=0 AND converted=1 AND date(timestamp)=?
            """, (store_id, today)).fetchone()[0] or 0

            today_rate = today_conv / today_uv if today_uv > 0 else None

            avg_7d = db.execute("""
                SELECT AVG(daily_rate) FROM (
                    SELECT date(timestamp) AS d,
                           CAST(SUM(CASE WHEN converted=1 THEN 1 ELSE 0 END) AS REAL)
                             / NULLIF(COUNT(DISTINCT CASE WHEN event_type='ENTRY' AND is_staff=0
                                                          THEN visitor_id END), 0) AS daily_rate
                    FROM events
                    WHERE store_id=?
                      AND date(timestamp) BETWEEN date('now','-7 days') AND date('now','-1 day')
                    GROUP BY d
                ) sub
            """, (store_id,)).fetchone()[0]

            if today_rate is not None and avg_7d and avg_7d > 0:
                ratio = today_rate / avg_7d
                if ratio < CONVERSION_CRIT_RATIO:
                    sev = "CRITICAL"
                elif ratio < CONVERSION_WARN_RATIO:
                    sev = "WARN"
                else:
                    sev = None
                if sev:
                    detected.append(Anomaly(
                        anomaly_id=str(uuid.uuid4()), store_id=store_id,
                        anomaly_type="CONVERSION_DROP", severity=sev,
                        description=f"Today's conversion {today_rate:.1%} vs 7d avg {avg_7d:.1%} "
                                    f"({ratio:.0%} of average)",
                        suggested_action="Conversion rate below average. Check staff availability "
                                         "or product display issues.",
                        detected_at=now_str,
                    ))

            # ── DEAD_ZONE ─────────────────────────────────────────────────────
            normal_zones = db.execute("""
                SELECT DISTINCT zone_id FROM events
                WHERE store_id=? AND event_type='ZONE_ENTER'
                  AND zone_id IS NOT NULL AND date(timestamp) < date('now')
                GROUP BY zone_id HAVING COUNT(*) > 5
            """, (store_id,)).fetchall()

            for row in normal_zones:
                zid = row[0]
                recent = db.execute("""
                    SELECT COUNT(*) FROM events
                    WHERE store_id=? AND event_type='ZONE_ENTER' AND zone_id=?
                      AND timestamp >= datetime('now',? || ' minutes')
                """, (store_id, zid, f"-{DEAD_ZONE_MINUTES}")).fetchone()[0] or 0
                if recent == 0:
                    detected.append(Anomaly(
                        anomaly_id=str(uuid.uuid4()), store_id=store_id,
                        anomaly_type="DEAD_ZONE", severity="INFO",
                        description=f"Zone {zid} has had no visitors in {DEAD_ZONE_MINUTES} minutes",
                        suggested_action=f"Zone {zid} has no visitors. Consider repositioning "
                                          "display or checking signage.",
                        detected_at=now_str,
                    ))

    except Exception as e:
        return JSONResponse(
            {"error": "database_unavailable", "detail": str(e), "retry_after": 30},
            status_code=503,
        )

    return AnomaliesResponse(store_id=store_id, anomalies=detected).model_dump()
