"""GET /health — service status, DB ping, per-store stale feed check."""

import time
from flask import Blueprint, jsonify
from database import get_db, ping
from models import HealthResponse, StoreHealth

health_bp = Blueprint("health", __name__)

_START_TIME = time.time()
API_VERSION = "1.0.0"
STALE_FEED_MINUTES = 10


@health_bp.route("/health")
def health():
    uptime = round(time.time() - _START_TIME, 1)

    # DB ping — must not crash even if DB is down
    db_latency, db_error = None, None
    try:
        db_latency = ping()
    except Exception as e:
        db_error = str(e)

    stores: list[StoreHealth] = []
    if db_error is None:
        try:
            with get_db() as db:
                store_ids = [r[0] for r in db.execute(
                    "SELECT DISTINCT store_id FROM events"
                ).fetchall()]

                for sid in store_ids:
                    last_ts = db.execute("""
                        SELECT MAX(timestamp) FROM events WHERE store_id=?
                    """, (sid,)).fetchone()[0]

                    events_1h = db.execute("""
                        SELECT COUNT(*) FROM events
                        WHERE store_id=? AND timestamp >= datetime('now','-1 hour')
                    """, (sid,)).fetchone()[0] or 0

                    stale = False
                    if last_ts:
                        try:
                            last_epoch = time.mktime(
                                time.strptime(last_ts[:19], "%Y-%m-%dT%H:%M:%S")
                            )
                            stale = (time.time() - last_epoch) > STALE_FEED_MINUTES * 60
                        except Exception:
                            stale = True

                    stores.append(StoreHealth(
                        store_id=sid,
                        last_event_ts=last_ts,
                        events_last_hour=events_1h,
                        stale_feed=stale,
                    ))
        except Exception as e:
            db_error = str(e)

    status = "UP" if db_error is None else "DOWN"
    resp = HealthResponse(
        status=status,
        db_latency_ms=db_latency,
        db_error=db_error,
        uptime_seconds=uptime,
        api_version=API_VERSION,
        stores=stores,
    )
    http_code = 200 if status == "UP" else 503
    return jsonify(resp.model_dump()), http_code
