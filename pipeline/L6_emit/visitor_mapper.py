"""
Builds the canonical (camera_id, track_id) → visitor_id lookup table.

Sources:
  L4 ENTRY/EXIT events  → entry camera track_id → VIS_xxxxxx
  L5 VISITOR_LINKED     → zone camera track_id  → VIS_xxxxxx (cross-cam Re-ID)
  L5 REENTRY            → new track_id          → original VIS_xxxxxx
"""


class VisitorMapper:
    def __init__(self):
        # (camera_id, track_id) → canonical visitor_id
        self._map: dict[tuple, str] = {}
        # VIS_xxx (L4-assigned for re-entry new track) → original VIS_xxx
        self._reentry_alias: dict[str, str] = {}

    def build(self, all_events: list[dict]) -> None:
        # Pass 1 — register all direct mappings from L4 and L5
        for ev in all_events:
            etype  = ev.get("event_type", "")
            cam    = ev.get("camera_id")
            vid    = ev.get("visitor_id")
            meta   = ev.get("metadata", {})
            tid    = meta.get("track_id")

            if not cam or not vid or tid is None:
                continue

            if etype in ("ENTRY", "EXIT"):
                self._map[(cam, tid)] = vid

            elif etype == "VISITOR_LINKED":
                # zone cam track linked to entry cam visitor
                orig = meta.get("original_visitor_id") or vid
                self._map[(cam, tid)] = orig

            elif etype == "REENTRY":
                # new_visitor_id is the L4-assigned ID that should map to original
                new_vid = meta.get("new_visitor_id")
                if new_vid:
                    self._reentry_alias[new_vid] = vid  # vid = original

        # Pass 2 — resolve any aliases (e.g. ENTRY cam tracks that are re-entries)
        for key, vid in list(self._map.items()):
            canonical = self._reentry_alias.get(vid, vid)
            self._map[key] = canonical

        print(f"[visitor_mapper] {len(self._map)} track→visitor mappings  "
              f"| {len(self._reentry_alias)} re-entry aliases")

    def get(self, camera_id: str, track_id: int | None) -> str | None:
        if track_id is None:
            return None
        return self._map.get((camera_id, track_id))

    def resolve_alias(self, visitor_id: str) -> str:
        """Return the canonical visitor_id (resolves re-entry aliases)."""
        return self._reentry_alias.get(visitor_id, visitor_id)
