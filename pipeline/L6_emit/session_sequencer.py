"""
Per-visitor session sequence counter.

seq starts at 1 for each visitor and increments with every event.
On REENTRY the counter continues from where it left off — it never resets.
"""

from collections import defaultdict


class SessionSequencer:
    def __init__(self):
        self._counters: dict[str, int] = defaultdict(int)

    def next(self, visitor_id: str) -> int:
        self._counters[visitor_id] += 1
        return self._counters[visitor_id]

    def current(self, visitor_id: str) -> int:
        return self._counters[visitor_id]

    def set(self, visitor_id: str, value: int) -> None:
        """Force-set counter — used to continue a re-entry session."""
        self._counters[visitor_id] = value
