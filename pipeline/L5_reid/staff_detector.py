"""
Staff detection — three rules in priority order:

  1. BOH zone rule      — centroid exclusively in back-of-house > 2 min
  2. HSV colour mask    — torso matches known uniform colour
  3. Long-duration flag — L2 long_duration_flag + weak colour signal

Once a track is flagged as staff it stays staff for the session.
"""

import cv2
import numpy as np
from dataclasses import dataclass, field
from datetime import datetime

# ── Uniform colour ranges (HSV) ───────────────────────────────────────────────

UNIFORM_COLOURS: dict[str, dict] = {
    "black": {
        "lower": np.array([0,   0,   0]),
        "upper": np.array([180, 50, 80]),
    },
    "hot_pink": {
        "lower": np.array([140, 100, 100]),
        "upper": np.array([170, 255, 255]),
    },
    "dark_blue": {
        "lower": np.array([100,  60,  30]),
        "upper": np.array([130, 255, 150]),
    },
}

HSV_THRESHOLD          = 0.35   # fraction of torso pixels matching uniform colour
HSV_CONFIDENT_THRESHOLD = 0.50  # above this → certain without VLM
BOH_MIN_SECONDS        = 120    # 2 minutes exclusively in BOH


# ── Per-track state ───────────────────────────────────────────────────────────

@dataclass
class TrackStaffState:
    is_staff: bool | None = None        # None = not yet determined
    method: str | None = None
    confidence: float = 0.0
    vlm_checked: bool = False
    zone_history: list = field(default_factory=list)  # [(zone_id, ts), ...]
    first_seen: datetime | None = None
    last_seen: datetime | None = None


class StaffDetector:
    def __init__(self, boh_zones: list[str] | None = None):
        self._boh = set(boh_zones or [])
        self._states: dict[tuple, TrackStaffState] = {}  # (camera_id, track_id) → state

    def get_state(self, camera_id: str, track_id: int) -> TrackStaffState:
        key = (camera_id, track_id)
        if key not in self._states:
            self._states[key] = TrackStaffState()
        return self._states[key]

    def record_zone(self, camera_id: str, track_id: int,
                    zone_id: str | None, ts: datetime) -> None:
        st = self.get_state(camera_id, track_id)
        if zone_id:
            st.zone_history.append((zone_id, ts))

    def check(
        self,
        camera_id: str,
        track_id: int,
        crop_bgr: np.ndarray | None,
        track_meta: dict,
        current_ts: datetime,
    ) -> tuple[bool | None, str | None, float]:
        """
        Returns (is_staff, detection_method, confidence).
        is_staff=None means more info needed (call vlm_fallback).
        """
        st = self.get_state(camera_id, track_id)
        if st.first_seen is None:
            st.first_seen = current_ts
        st.last_seen = current_ts

        # Already determined
        if st.is_staff is not None:
            return st.is_staff, st.method, st.confidence

        # Rule 1 — BOH zone
        if self._boh and st.zone_history:
            non_boh = [z for z, _ in st.zone_history if z not in self._boh]
            if not non_boh and len(st.zone_history) >= 2:
                duration = (st.zone_history[-1][1] - st.zone_history[0][1]).total_seconds()
                if duration > BOH_MIN_SECONDS:
                    self._flag(st, True, "boh_rule", 0.95)
                    return True, "boh_rule", 0.95

        # Rule 2 — HSV colour
        if crop_bgr is not None and crop_bgr.size > 0:
            matched, colour, ratio = _check_hsv(crop_bgr)
            if matched and ratio >= HSV_CONFIDENT_THRESHOLD:
                self._flag(st, True, f"colour_{colour}", ratio)
                return True, f"colour_{colour}", ratio
            if matched and ratio >= HSV_THRESHOLD:
                # Ambiguous — request VLM check
                if not st.vlm_checked:
                    return None, f"colour_{colour}_ambiguous", ratio

        # Rule 3 — long duration flag + weak colour
        if track_meta.get("long_duration_flag"):
            if crop_bgr is not None:
                matched, colour, ratio = _check_hsv(crop_bgr)
                if matched:
                    self._flag(st, True, "long_duration_colour", ratio)
                    return True, "long_duration_colour", ratio

        # No signal strong enough
        if st.is_staff is None:
            self._flag(st, False, "no_signal", 0.6)
        return False, "no_signal", 0.6

    def apply_vlm_result(self, camera_id: str, track_id: int,
                         is_staff: bool, confidence: float) -> None:
        st = self.get_state(camera_id, track_id)
        st.vlm_checked = True
        method = "vlm_groq"
        self._flag(st, is_staff, method, confidence)

    @staticmethod
    def _flag(st: TrackStaffState, is_staff: bool, method: str, conf: float) -> None:
        st.is_staff = is_staff
        st.method = method
        st.confidence = conf


# ── HSV helper ────────────────────────────────────────────────────────────────

def _check_hsv(crop_bgr: np.ndarray) -> tuple[bool, str | None, float]:
    h = crop_bgr.shape[0]
    torso = crop_bgr[:int(h * 0.65), :]  # head + torso
    if torso.size == 0:
        return False, None, 0.0
    hsv = cv2.cvtColor(torso, cv2.COLOR_BGR2HSV)
    total = torso.shape[0] * torso.shape[1]
    best_colour, best_ratio = None, 0.0
    for name, ranges in UNIFORM_COLOURS.items():
        mask = cv2.inRange(hsv, ranges["lower"], ranges["upper"])
        ratio = float(np.sum(mask > 0)) / total
        if ratio > best_ratio:
            best_ratio, best_colour = ratio, name
    return best_ratio >= HSV_THRESHOLD, best_colour, best_ratio
