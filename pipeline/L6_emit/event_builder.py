"""
Assembles a raw event dict into the final validated PipelineEvent.

Fills every required field, looks up zone metadata from the layout,
upgrades ZONE_ENTER → BILLING_QUEUE_JOIN when queue_depth > 0 in a billing zone,
and validates via Pydantic before returning.

Returns (final_dict, None) on success or (None, rejection_reason) on failure.
"""

import uuid
from pydantic import ValidationError
from schemas import PipelineEvent, EventMetadata

_BILLING_EVENT_TYPES = {"BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON"}


def build_event(
    raw: dict,
    visitor_id: str | None,
    is_staff: bool,
    session_seq: int,
    zone_meta_map: dict,    # zone_id → {zone_name, zone_type, ...}
    billing_zone_ids: set[str],
    converted: bool = False,
) -> tuple[dict | None, str | None]:
    """
    Returns (final_event_dict, None) or (None, rejection_reason).
    """
    meta_in  = raw.get("metadata") or {}
    zone_id  = raw.get("zone_id")
    etype    = raw.get("event_type", "")

    # Upgrade ZONE_ENTER → BILLING_QUEUE_JOIN when in billing zone with queue
    if etype == "ZONE_ENTER" and zone_id:
        qd = meta_in.get("queue_depth") or 0
        if zone_id.upper() in {z.upper() for z in billing_zone_ids} and qd > 0:
            etype = "BILLING_QUEUE_JOIN"

    # Zone name lookup for sku_zone field
    zone_info = zone_meta_map.get(zone_id or "", {})
    sku_zone  = zone_info.get("zone_name") if zone_info else meta_in.get("sku_zone")

    metadata = EventMetadata(
        queue_depth = meta_in.get("queue_depth"),
        sku_zone    = sku_zone,
        session_seq = session_seq,
        group_id    = meta_in.get("group_id"),
        group_size  = meta_in.get("group_size"),
        track_id    = meta_in.get("track_id"),
    )

    try:
        ev = PipelineEvent(
            event_id   = str(uuid.uuid4()),
            store_id   = raw.get("store_id", ""),
            camera_id  = raw.get("camera_id", ""),
            visitor_id = visitor_id,
            event_type = etype,
            timestamp  = raw.get("timestamp", ""),
            zone_id    = zone_id,
            dwell_ms   = max(0, int(raw.get("dwell_ms") or 0)),
            is_staff   = is_staff,
            confidence = float(raw.get("confidence") or 0.0),
            converted  = converted,
            metadata   = metadata,
        )
        return ev.model_dump(), None

    except (ValidationError, Exception) as e:
        return None, str(e)
