"""
Re-entry / cross-camera linking orchestrator.

Bridges L4 ENTRY/EXIT events with the embedding cache.

Flow:
  on_entry(visitor_id, track_id, camera_id, embedding, ts)
    → check reentry_pool  → REENTRY event  (person returned after leaving)
    → check session_pool  → VISITOR_LINKED (same person, different camera)
    → else                → confirmed new ENTRY

  on_exit(visitor_id, camera_id, ts)
    → move embedding to exit pool so re-entry can be detected later
"""

from datetime import datetime
import numpy as np

from reid_cache import ReIDCache


class ReentryHandler:
    def __init__(self, store_id: str, cache: ReIDCache):
        self._store = store_id
        self._cache = cache
        # (camera_id, track_id) → visitor_id — for cross-cam attribution
        self._track_map: dict[tuple, str] = {}

    def get_visitor_id(self, camera_id: str, track_id: int) -> str | None:
        return self._track_map.get((camera_id, track_id))

    def on_entry(
        self,
        visitor_id: str,
        track_id: int,
        camera_id: str,
        embedding: np.ndarray | None,
        ts: datetime,
    ) -> dict:
        """
        Called when L4 emits ENTRY or when a new track appears on a zone camera.

        Returns an event dict with event_type:
          ENTRY         — new visitor confirmed
          REENTRY       — same person returning after exit
          VISITOR_LINKED — same person already active on a different camera
        """
        if embedding is None:
            # No embedding available — register track but skip cache comparisons
            self._track_map[(camera_id, track_id)] = visitor_id
            return {"event_type": "ENTRY", "visitor_id": visitor_id,
                    "similarity_score": None, "original_visitor_id": None}

        # 1 — re-entry check (past exit)
        match_id, score = self._cache.find_reentry(embedding, self._store, ts)
        if match_id:
            self._track_map[(camera_id, track_id)] = match_id
            self._cache.add_entry(match_id, self._store, camera_id, embedding, ts)
            return {
                "event_type": "REENTRY",
                "visitor_id": match_id,
                "new_visitor_id": visitor_id,
                "similarity_score": round(score, 4),
                "original_visitor_id": match_id,
            }

        # 2 — cross-camera check (active session, different camera)
        cc_id, cc_score = self._cache.find_cross_camera(
            embedding, self._store, camera_id, ts
        )
        if cc_id:
            self._track_map[(camera_id, track_id)] = cc_id
            return {
                "event_type": "VISITOR_LINKED",
                "visitor_id": cc_id,
                "new_visitor_id": visitor_id,
                "similarity_score": round(cc_score, 4),
                "original_visitor_id": cc_id,
            }

        # 3 — genuinely new visitor
        self._track_map[(camera_id, track_id)] = visitor_id
        self._cache.add_entry(visitor_id, self._store, camera_id, embedding, ts)
        return {"event_type": "ENTRY", "visitor_id": visitor_id,
                "similarity_score": None, "original_visitor_id": None}

    def on_exit(self, visitor_id: str, ts: datetime) -> None:
        self._cache.mark_exit(visitor_id, self._store, ts)
