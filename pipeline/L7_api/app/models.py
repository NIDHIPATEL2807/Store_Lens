"""Pydantic models — incoming events and all API response shapes."""

from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, field_validator


# ── Incoming event (must match L6 output schema exactly) ─────────────────────

class EventMetadata(BaseModel):
    queue_depth:  Optional[int]   = None
    sku_zone:     Optional[str]   = None
    session_seq:  int             = 1
    group_id:     Optional[str]   = None
    group_size:   Optional[int]   = None
    track_id:     Optional[int]   = None


class IncomingEvent(BaseModel):
    event_id:   str
    store_id:   str
    camera_id:  str
    visitor_id: Optional[str]   = None
    event_type: str
    timestamp:  str
    zone_id:    Optional[str]   = None
    dwell_ms:   int             = 0
    is_staff:   bool            = False
    confidence: float           = 0.0
    converted:  bool            = False
    metadata:   EventMetadata   = EventMetadata()

    @field_validator("confidence")
    @classmethod
    def clamp_conf(cls, v: float) -> float:
        return round(min(1.0, max(0.0, float(v))), 4)

    @field_validator("dwell_ms")
    @classmethod
    def non_neg(cls, v: int) -> int:
        return max(0, int(v))


# ── Ingest response ───────────────────────────────────────────────────────────

class RejectedEvent(BaseModel):
    index:  int
    reason: str
    raw:    dict

class IngestResponse(BaseModel):
    accepted:  int
    rejected:  int
    rejections: list[RejectedEvent] = []


# ── Metrics ───────────────────────────────────────────────────────────────────

class ZoneDwell(BaseModel):
    zone_id:      str
    avg_dwell_ms: float

class MetricsResponse(BaseModel):
    store_id:         str
    date:             str
    unique_visitors:  int
    conversion_rate:  float
    avg_dwell_by_zone: list[ZoneDwell]
    current_queue_depth: int
    abandonment_rate: float


# ── Funnel ────────────────────────────────────────────────────────────────────

class FunnelStage(BaseModel):
    stage:    str
    count:    int
    dropoff_pct: float

class FunnelResponse(BaseModel):
    store_id: str
    date:     str
    stages:   list[FunnelStage]


# ── Heatmap ───────────────────────────────────────────────────────────────────

class HeatmapZone(BaseModel):
    zone_id:         str
    zone_name:       Optional[str]
    visit_count:     int
    avg_dwell_ms:    float
    score:           float        # 0–100 normalised
    data_confidence: str          # "OK" or "LOW"

class HeatmapResponse(BaseModel):
    store_id: str
    date:     str
    zones:    list[HeatmapZone]


# ── Anomalies ─────────────────────────────────────────────────────────────────

class Anomaly(BaseModel):
    anomaly_id:       str
    store_id:         str
    anomaly_type:     str
    severity:         str   # "INFO" | "WARN" | "CRITICAL"
    description:      str
    suggested_action: str
    detected_at:      str
    resolved:         bool = False

class AnomaliesResponse(BaseModel):
    store_id:  str
    anomalies: list[Anomaly]


# ── Health ────────────────────────────────────────────────────────────────────

class StoreHealth(BaseModel):
    store_id:          str
    last_event_ts:     Optional[str]
    events_last_hour:  int
    stale_feed:        bool

class HealthResponse(BaseModel):
    status:        str    # "UP" | "DOWN"
    db_latency_ms: Optional[float]
    db_error:      Optional[str]
    uptime_seconds: float
    api_version:   str
    stores:        list[StoreHealth]
