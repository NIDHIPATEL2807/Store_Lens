"""
Group entry detection.

Tracks recent ENTRY timestamps.  When a new entry fires, if another entry
was emitted within GROUP_WINDOW_SECONDS, both belong to the same group.
The first person in a group is emitted with group_id=None (we don't yet
know others are coming); all subsequent arrivals within the window share
the same group_id.  Downstream systems can correlate by timestamp window.
"""

import uuid
from datetime import datetime


GROUP_WINDOW_SECONDS = 2.0


class GroupDetector:
    def __init__(self):
        self._recent: list[dict] = []  # {"ts": datetime, "group_id": str | None}

    def check_group(self, current_ts: datetime) -> tuple[str | None, int]:
        """
        Call before emitting an ENTRY event.

        Returns:
            (group_id, group_size) — group_id is None if this is a solo entry.
        """
        self._prune(current_ts)
        if not self._recent:
            return None, 1

        # Reuse an existing group_id if one was already assigned in this window,
        # otherwise create a new one so all members share the same token.
        existing_gid = next(
            (e["group_id"] for e in self._recent if e.get("group_id")), None
        )
        gid = existing_gid or f"G_{uuid.uuid4().hex[:6]}"
        return gid, len(self._recent) + 1

    def record_entry(self, ts: datetime, group_id: str | None) -> None:
        self._recent.append({"ts": ts, "group_id": group_id})
        self._prune(ts)

    def _prune(self, now: datetime) -> None:
        cutoff = now.timestamp() - GROUP_WINDOW_SECONDS
        self._recent = [e for e in self._recent if e["ts"].timestamp() >= cutoff]
