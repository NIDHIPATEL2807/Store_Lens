"""POST /events/ingest — batch ingest with dedup, partial success."""

import json
from flask import Blueprint, request, jsonify
from pydantic import ValidationError

from database import get_db
from models import IncomingEvent, IngestResponse, RejectedEvent

ingestion_bp = Blueprint("ingestion", __name__)

_INSERT = """
INSERT OR IGNORE INTO events
    (event_id, store_id, camera_id, visitor_id, event_type,
     timestamp, zone_id, dwell_ms, is_staff, confidence,
     converted, metadata)
VALUES (?,?,?,?,?, ?,?,?,?,?, ?,?)
"""


@ingestion_bp.route("/events/ingest", methods=["POST"])
def ingest():
    body = request.get_json(force=True, silent=True)
    if body is None:
        return jsonify({"error": "invalid_json"}), 400

    # Accept both array and single object
    if isinstance(body, dict):
        body = [body]
    if not isinstance(body, list):
        return jsonify({"error": "expected_array_or_object"}), 400

    if len(body) > 500:
        return jsonify({"error": "batch_too_large", "max": 500}), 400

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
        return jsonify({"error": "database_unavailable", "detail": str(e), "retry_after": 30}), 503

    resp = IngestResponse(accepted=accepted, rejected=len(rejected), rejections=rejected)
    status = 207 if rejected else 200
    return jsonify(resp.model_dump()), status
