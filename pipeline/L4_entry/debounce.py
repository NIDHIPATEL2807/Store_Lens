"""
Crossing debounce and re-entry suppression.

Uses side-stability rather than movement.crosses() counting because
movement.crosses() fires for exactly 1 frame per real crossing — a 2/3
window would never trigger for genuine entries.  Instead we track which
side of the entry line the centroid is on each frame and require it to
stay on the *new* side for SIDE_CONFIRM_FRAMES consecutive frames before
committing a crossing.  This naturally rejects jitter from people walking
parallel to the threshold (their side oscillates; it never stabilises).
"""

from collections import defaultdict, deque
from datetime import datetime


SIDE_CONFIRM_FRAMES = 2         # consecutive frames on new side to confirm a crossing
REENTRY_SUPPRESSION_SECONDS = 30  # suppress a second ENTRY for the same track within this window


class SideDebouncer:
    """
    Per-track side tracker.  Confirms crossings once a side is stable for
    SIDE_CONFIRM_FRAMES frames.  Suppresses ENTRY re-fires within the
    suppression window.
    """

    def __init__(self):
        self._side_history: dict[int, deque] = defaultdict(
            lambda: deque(maxlen=SIDE_CONFIRM_FRAMES + 1)
        )
        self._confirmed_side: dict[int, str | None] = {}
        self._last_entry_ts: dict[int, datetime] = {}

    def initialize(self, track_id: int, initial_side: str) -> None:
        """
        Seed the tracker for a newly-seen track without triggering a crossing.
        Called on first appearance so the initial side is treated as baseline.
        """
        h = self._side_history[track_id]
        for _ in range(SIDE_CONFIRM_FRAMES):
            h.append(initial_side)
        self._confirmed_side[track_id] = initial_side

    def update(self, track_id: int, current_side: str) -> str | None:
        """
        Record the current side for this track.

        Returns:
            "ENTRY"  — crossing to inside confirmed
            "EXIT"   — crossing to outside confirmed
            None     — no confirmed crossing this frame
        """
        history = self._side_history[track_id]
        confirmed = self._confirmed_side.get(track_id)

        history.append(current_side)

        if len(history) < SIDE_CONFIRM_FRAMES:
            return None

        # Are the last N frames all the same side?
        recent = list(history)[-SIDE_CONFIRM_FRAMES:]
        if len(set(recent)) != 1:
            return None  # inconsistent — still transitioning

        new_side = recent[0]

        if confirmed is None:
            # First stable read — set baseline, no crossing
            self._confirmed_side[track_id] = new_side
            return None

        if new_side != confirmed:
            self._confirmed_side[track_id] = new_side
            return "ENTRY" if new_side == "inside" else "EXIT"

        return None

    def record_entry_emit(self, track_id: int, ts: datetime) -> None:
        self._last_entry_ts[track_id] = ts

    def can_emit_entry(self, track_id: int, current_ts: datetime) -> bool:
        last = self._last_entry_ts.get(track_id)
        if last is None:
            return True
        return (current_ts - last).total_seconds() > REENTRY_SUPPRESSION_SECONDS

    def remove(self, track_id: int) -> None:
        self._side_history.pop(track_id, None)
        self._confirmed_side.pop(track_id, None)
        self._last_entry_ts.pop(track_id, None)
