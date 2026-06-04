"""GET /stores/{store_id}/heatmap — zone visit frequency normalised 0-100."""

import time
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse

import database
from models import HeatmapResponse, HeatmapZone

router = APIRouter()


@router.get("/stores/{store_id}/heatmap")
def get_heatmap(store_id: str, date: Optional[str] = None):
    try:
        with database.get_db() as db:
            exists = db.execute(
                "SELECT 1 FROM events WHERE store_id=? LIMIT 1", (store_id,)
            ).fetchone()
            if not exists:
                return JSONResponse({"error": "store_not_found", "store_id": store_id}, status_code=404)

            if date is None:
                date = db.execute(
                    "SELECT date(MAX(timestamp)) FROM events WHERE store_id=?", (store_id,)
                ).fetchone()[0] or time.strftime("%Y-%m-%d", time.gmtime())

            rows = db.execute("""
                SELECT zone_id, COUNT(*) AS visit_count, AVG(dwell_ms) AS avg_dwell_ms
                FROM events
                WHERE store_id=? AND is_staff=0
                  AND event_type IN ('ZONE_ENTER','ZONE_DWELL')
                  AND zone_id IS NOT NULL AND date(timestamp)=?
                GROUP BY zone_id
            """, (store_id, date)).fetchall()

            total_sessions = db.execute("""
                SELECT COUNT(DISTINCT visitor_id) FROM events
                WHERE store_id=? AND is_staff=0 AND visitor_id IS NOT NULL
                  AND event_type='ENTRY' AND date(timestamp)=?
            """, (store_id, date)).fetchone()[0] or 0

    except Exception as e:
        return JSONResponse(
            {"error": "database_unavailable", "detail": str(e), "retry_after": 30},
            status_code=503,
        )

    if not rows:
        return HeatmapResponse(store_id=store_id, date=date, zones=[]).model_dump()

    data_confidence = "LOW" if total_sessions < 20 else "OK"
    visits = [r["visit_count"] for r in rows]
    min_v, max_v = min(visits), max(visits)
    span = max_v - min_v or 1

    zones = []
    for r in rows:
        score = round((r["visit_count"] - min_v) / span * 100, 1)
        zones.append(HeatmapZone(
            zone_id         = r["zone_id"],
            zone_name       = r["zone_id"],
            visit_count     = r["visit_count"],
            avg_dwell_ms    = round(r["avg_dwell_ms"] or 0, 1),
            score           = score,
            data_confidence = data_confidence,
        ))

    zones.sort(key=lambda z: z.score, reverse=True)
    return HeatmapResponse(store_id=store_id, date=date, zones=zones).model_dump()
