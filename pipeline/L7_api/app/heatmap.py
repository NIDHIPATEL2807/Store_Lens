"""GET /stores/<store_id>/heatmap — zone visit frequency normalised 0-100."""

import time
from flask import Blueprint, jsonify
from database import get_db
from models import HeatmapResponse, HeatmapZone

heatmap_bp = Blueprint("heatmap", __name__)

# Zone types excluded from heatmap (threshold / non-revenue)
_EXCLUDED_TYPES = {"entry", "outside"}


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


@heatmap_bp.route("/stores/<store_id>/heatmap")
def get_heatmap(store_id: str):
    today = _today()
    try:
        with get_db() as db:
            exists = db.execute(
                "SELECT 1 FROM events WHERE store_id=? LIMIT 1", (store_id,)
            ).fetchone()
            if not exists:
                return jsonify({"error": "store_not_found", "store_id": store_id}), 404

            rows = db.execute("""
                SELECT zone_id,
                       COUNT(*)          AS visit_count,
                       AVG(dwell_ms)     AS avg_dwell_ms
                FROM events
                WHERE store_id=? AND is_staff=0
                  AND event_type IN ('ZONE_ENTER','ZONE_DWELL')
                  AND zone_id IS NOT NULL
                  AND date(timestamp)=?
                GROUP BY zone_id
            """, (store_id, today)).fetchall()

            total_sessions = db.execute("""
                SELECT COUNT(DISTINCT visitor_id) FROM events
                WHERE store_id=? AND is_staff=0 AND visitor_id IS NOT NULL
                  AND event_type='ENTRY' AND date(timestamp)=?
            """, (store_id, today)).fetchone()[0] or 0

    except Exception as e:
        return jsonify({"error": "database_unavailable", "detail": str(e), "retry_after": 30}), 503

    if not rows:
        return jsonify(HeatmapResponse(store_id=store_id, date=today, zones=[]).model_dump())

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
    resp = HeatmapResponse(store_id=store_id, date=today, zones=zones)
    return jsonify(resp.model_dump())
