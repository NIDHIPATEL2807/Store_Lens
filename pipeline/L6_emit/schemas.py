"""
Pydantic schema for the final pipeline event.
Every event written to events.jsonl is validated against this model.
"""

from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, field_validator, model_validator

VALID_EVENT_TYPES = {
    "ENTRY", "EXIT", "REENTRY",
    "ZONE_ENTER", "ZONE_EXIT", "ZONE_DWELL",
    "BILLING_QUEUE_JOIN", "BILLING_QUEUE_ABANDON",
    "STAFF_FLAGGED",
}


class EventMetadata(BaseModel):
    queue_depth:  Optional[int]   = None
    sku_zone:     Optional[str]   = None
    session_seq:  int             = 1
    group_id:     Optional[str]   = None
    group_size:   Optional[int]   = None
    track_id:     Optional[int]   = None

    @field_validator("session_seq")
    @classmethod
    def seq_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"session_seq must be ≥ 1, got {v}")
        return v


class PipelineEvent(BaseModel):
    event_id:   str
    store_id:   str
    camera_id:  str
    visitor_id: Optional[str]   = None
    event_type: str
    timestamp:  str
    zone_id:    Optional[str]   = None
    dwell_ms:   int             = 0
    is_staff:   bool            = False
    confidence: float
    converted:  bool            = False
    metadata:   EventMetadata

    @field_validator("event_type")
    @classmethod
    def valid_type(cls, v: str) -> str:
        if v not in VALID_EVENT_TYPES:
            raise ValueError(f"Unknown event_type: {v!r}")
        return v

    @field_validator("confidence")
    @classmethod
    def valid_conf(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence must be 0–1, got {v}")
        return round(v, 4)

    @field_validator("dwell_ms")
    @classmethod
    def non_neg_dwell(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"dwell_ms must be ≥ 0, got {v}")
        return v

    @model_validator(mode="after")
    def visitor_required_for_customers(self) -> PipelineEvent:
        if not self.is_staff and self.visitor_id is None:
            if self.event_type not in ("STAFF_FLAGGED",):
                raise ValueError("visitor_id is required for non-staff events")
        return self
