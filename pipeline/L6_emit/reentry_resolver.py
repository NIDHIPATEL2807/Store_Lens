"""
Handles REENTRY events from L5.

Two responsibilities:
  1. Identify which L4 ENTRY events are duplicates and should be suppressed
     (L5 detected that the new track_id belongs to an existing visitor).
  2. Provide the session_seq continuation value so REENTRY picks up where
     the previous session left off.
"""


class ReentryResolver:
    def __init__(self):
        # visitor_ids that were assigned by L4 but superseded by REENTRY
        # These correspond to ENTRY events in L4 that must be dropped.
        self._suppressed_visitor_ids: set[str] = set()

        # original_visitor_id → last known session_seq before re-entry
        self._continuation_seq: dict[str, int] = {}

    def build(self, all_events: list[dict]) -> None:
        for ev in all_events:
            if ev.get("event_type") != "REENTRY":
                continue
            meta    = ev.get("metadata", {})
            new_vid = meta.get("new_visitor_id")
            orig_vid = ev.get("visitor_id")          # the canonical visitor_id to keep
            if new_vid:
                self._suppressed_visitor_ids.add(new_vid)
            # Will fill continuation_seq during processing (from session_sequencer)

        print(f"[reentry_resolver] {len(self._suppressed_visitor_ids)} "
              f"suppressed L4 ENTRY visitor_id(s)")

    def is_suppressed(self, event: dict) -> bool:
        """
        True if this event is a redundant L4 ENTRY that L5's REENTRY replaces.
        Suppress the ENTRY but let the REENTRY event itself pass through.
        """
        if event.get("event_type") != "ENTRY":
            return False
        vid = event.get("visitor_id", "")
        return vid in self._suppressed_visitor_ids

    def set_continuation(self, original_visitor_id: str, seq: int) -> None:
        self._continuation_seq[original_visitor_id] = seq

    def get_continuation(self, original_visitor_id: str) -> int | None:
        return self._continuation_seq.get(original_visitor_id)
