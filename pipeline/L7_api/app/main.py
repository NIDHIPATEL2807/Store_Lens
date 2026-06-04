"""FastAPI app. Run with:  uvicorn app.main:app  or  python app/main.py"""

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

import json
import glob
from pathlib import Path

import database
from database           import init_db
from ingestion          import router as ingestion_router
from metrics            import router as metrics_router
from funnel             import router as funnel_router
from heatmap            import router as heatmap_router
from anomalies          import router as anomalies_router
from health             import router as health_router
from logging_middleware import LoggingMiddleware


_INSERT = """
INSERT OR IGNORE INTO events
    (event_id, store_id, camera_id, visitor_id, event_type,
     timestamp, zone_id, dwell_ms, is_staff, confidence, converted, metadata)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
"""

def _auto_ingest() -> None:
    """On cold start (empty DB), load all events.jsonl files automatically."""
    with database.get_db() as db:
        count = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        if count > 0:
            return

    # Works both locally (../../L6_emit) and in Docker (/api/L6_emit)
    base = Path(__file__).parent.parent  # /api when in Docker, pipeline/L7_api locally
    pattern = str(base / "L6_emit" / "output" / "**" / "events.jsonl")
    files = glob.glob(pattern, recursive=True)
    if not files:
        return

    total = 0
    with database.get_db() as db:
        for fpath in files:
            with open(fpath, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                        meta = e.get("metadata") or {}
                        db.execute(_INSERT, (
                            e.get("event_id"), e.get("store_id"), e.get("camera_id"),
                            e.get("visitor_id"), e.get("event_type"), e.get("timestamp"),
                            e.get("zone_id"), e.get("dwell_ms", 0),
                            int(bool(e.get("is_staff", False))),
                            e.get("confidence", 0.0),
                            int(bool(e.get("converted", False))),
                            json.dumps(meta),
                        ))
                        total += 1
                    except Exception:
                        pass
    print(f"[startup] auto-ingested {total} events from {len(files)} file(s)")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _auto_ingest()
    yield


app = FastAPI(title="Purplle Store Analytics API", version="1.0.0", lifespan=lifespan)

app.add_middleware(LoggingMiddleware)

for router in (
    ingestion_router, metrics_router, funnel_router,
    heatmap_router, anomalies_router, health_router,
):
    app.include_router(router)


@app.exception_handler(Exception)
async def _unhandled(request, exc):
    import traceback, logging, json
    logging.getLogger("api").error(json.dumps({
        "error":     str(exc),
        "traceback": traceback.format_exc()[-500:],
    }))
    return JSONResponse({"error": "internal_server_error", "detail": str(exc)}, status_code=500)


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    debug = os.getenv("DEBUG", "false").lower() == "true"
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=debug)
