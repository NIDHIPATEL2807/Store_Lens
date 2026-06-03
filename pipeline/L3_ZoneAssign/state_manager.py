"""
Per-track state for L3 zone assignment.

Keyed by (camera_id, track_id) so the same integer track_id on different
cameras never collides — important when multiple cameras cover the same store.
"""

from dataclasses import dataclass, field
from typing import Optional

# A zone change is only committed after the centroid has been consistently
# in the new zone for this many processed frames in a row.
# At frame_skip=3 on a 30 fps source, 3 frames = ~0.3 s — enough to suppress
# single-frame boundary jitter without delaying real transitions.
ZONE_CHANGE_DEBOUNCE_FRAMES: int = 3


@dataclass
class TrackState:
    # zone the track is currently in (None = open floor / between zones)
    current_zone_id:    Optional[str] = None
    current_zone_name:  Optional[str] = None
    current_zone_type:  Optional[str] = None
    is_revenue_zone:    bool          = False

    # when did this track enter the current zone
    zone_enter_ts:      Optional[str] = None

    # last frame timestamp we saw this track (used for disappearance detection)
    last_seen_ts:       str   = ""

    # last known detection confidence — used on disappearance-triggered exits
    last_confidence:    float = 0.0

    # dwell counters for the current zone
    total_dwell_ms:     float = 0.0
    last_dwell_emit_ms: float = 0.0

    # time spent in open floor (no zone) — tracked for analytics, not emitted
    floor_time_ms:      float = 0.0

    # how many zone sessions this track has had (increments on each new ZONE_ENTER)
    session_seq:        int   = 0

    # debounce: candidate zone the track seems to be moving into
    # None means "same as current_zone_id" (no pending transition)
    pending_zone_id:    Optional[str]  = None
    pending_zone:       Optional[dict] = None   # full zone dict for the candidate
    pending_frames:     int            = 0


class StateManager:
    def __init__(self) -> None:
        # (camera_id, track_id) → TrackState
        self._states: dict[tuple[str, int], TrackState] = {}

    # ── basic CRUD ────────────────────────────────────────────────────────────

    def get(self, camera_id: str, track_id: int) -> TrackState:
        key = (camera_id, track_id)
        if key not in self._states:
            self._states[key] = TrackState()
        return self._states[key]

    def has(self, camera_id: str, track_id: int) -> bool:
        return (camera_id, track_id) in self._states

    def remove(self, camera_id: str, track_id: int) -> None:
        self._states.pop((camera_id, track_id), None)

    def all_keys(self) -> list[tuple[str, int]]:
        return list(self._states.keys())

    # ── zone helpers ──────────────────────────────────────────────────────────

    def enter_zone(self, camera_id: str, track_id: int, zone: dict, ts: str) -> None:
        """Transition a track into a new zone and reset dwell counters."""
        state = self.get(camera_id, track_id)
        state.session_seq        += 1
        state.current_zone_id    = zone["zone_id"]
        state.current_zone_name  = zone.get("zone_name", zone["zone_id"])
        state.current_zone_type  = zone.get("zone_type", "")
        state.is_revenue_zone    = zone.get("is_revenue_zone", False)
        state.zone_enter_ts      = ts
        state.total_dwell_ms     = 0.0
        state.last_dwell_emit_ms = 0.0

    def exit_zone(self, camera_id: str, track_id: int) -> None:
        """Clear zone fields but keep the track alive (it may re-enter)."""
        state = self.get(camera_id, track_id)
        state.current_zone_id    = None
        state.current_zone_name  = None
        state.current_zone_type  = None
        state.is_revenue_zone    = False
        state.zone_enter_ts      = None
        state.total_dwell_ms     = 0.0
        state.last_dwell_emit_ms = 0.0

    # ── billing queue ─────────────────────────────────────────────────────────

    def billing_queue_depth(self, camera_id: str, exclude_track_id: int) -> int:
        """
        Count tracks on the same camera that are currently in a billing zone,
        excluding the querying track itself.
        """
        return sum(
            1
            for (cam, tid), state in self._states.items()
            if cam == camera_id
            and tid != exclude_track_id
            and state.current_zone_type == "billing"
        )
