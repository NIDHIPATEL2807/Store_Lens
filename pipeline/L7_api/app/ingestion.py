"""POST /events/ingest — batch ingest with dedup, partial success."""

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from database import get_db
from models import IncomingEvent, IngestResponse, RejectedEvent

router = APIRouter()

_INSERT = """
INSERT OR IGNORE INTO events
    (event_id, store_id, camera_id, visitor_id, event_type,
     timestamp, zone_id, dwell_ms, is_staff, confidence,
     converted, metadata)
VALUES (?,?,?,?,?, ?,?,?,?,?, ?,?)
"""


@router.post("/events/ingest")
async def ingest(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid_json"}, status_code=400)

    if isinstance(body, dict):
        body = [body]
    if not isinstance(body, list):
        return JSONResponse({"error": "expected_array_or_object"}, status_code=400)

    if len(body) > 500:
        return JSONResponse({"error": "batch_too_large", "max": 500}, status_code=400)

    accepted, rejected = 0, []

    try:
        with get_db() as db:
            for i, raw in enumerate(body):
                try:
                    ev = IncomingEvent.model_validate(raw)
                except (ValidationError, Exception) as e:
                    rejected.append(RejectedEvent(index=i, reason=str(e), raw=raw))
                    continue

                db.execute(_INSERT, (
                    ev.event_id, ev.store_id, ev.camera_id, ev.visitor_id, ev.event_type,
                    ev.timestamp, ev.zone_id, ev.dwell_ms, int(ev.is_staff), ev.confidence,
                    int(ev.converted), json.dumps(ev.metadata.model_dump()),
                ))
                accepted += 1
    except Exception as e:
        return JSONResponse(
            {"error": "database_unavailable", "detail": str(e), "retry_after": 30},
            status_code=503,
        )

    resp = IngestResponse(accepted=accepted, rejected=len(rejected), rejections=rejected)
    status = 207 if rejected else 200
    return JSONResponse(resp.model_dump(), status_code=status)
