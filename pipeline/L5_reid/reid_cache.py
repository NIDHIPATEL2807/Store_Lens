"""
Cross-camera embedding cache for Re-ID.

Two pools:
  session_pool   — current session embeddings keyed by (store_id, visitor_id).
                   Used for cross-camera linking (CAM1 track → CAM3 visitor_id).
                   No time expiry within a session.

  reentry_pool   — embeddings from people who exited.  Searched when a new ENTRY
                   fires to detect re-entry.  10-minute expiry window.

Match types returned:
  "reentry"       — new entry matches a past exit (same person returned)
  "cross_camera"  — new track on a different camera matches an active visitor
  None            — no match
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import numpy as np

from embedder import cosine_similarity

REENTRY_WINDOW_MINUTES  = 10
SIMILARITY_THRESHOLD    = 0.75   # cosine sim required to claim a match
CROSS_CAM_THRESHOLD     = 0.72   # slightly lower for cross-camera (different viewpoint)


@dataclass
class CachedEntry:
    visitor_id: str
    store_id: str
    camera_id: str
    embedding: np.ndarray
    ts: datetime


class ReIDCache:
    def __init__(self):
        # (store_id, visitor_id) → CachedEntry — active visitors (not yet exited)
        self._session: dict[tuple, CachedEntry] = {}
        # list of CachedEntry — recent exits for re-entry matching
        self._exits: list[CachedEntry] = []

    # ── Insertion ─────────────────────────────────────────────────────────────

    def add_entry(self, visitor_id: str, store_id: str, camera_id: str,
                  embedding: np.ndarray, ts: datetime) -> None:
        """Register a new entry (person just entered — add to active session pool)."""
        key = (store_id, visitor_id)
        self._session[key] = CachedEntry(visitor_id, store_id, camera_id, embedding, ts)

    def mark_exit(self, visitor_id: str, store_id: str, ts: datetime) -> None:
        """Move a visitor from session pool to exit pool when they leave."""
        key = (store_id, visitor_id)
        entry = self._session.pop(key, None)
        if entry:
            entry.ts = ts
            self._exits.append(entry)
        self._prune_exits(ts)

    # ── Lookup ────────────────────────────────────────────────────────────────

    def find_reentry(self, embedding: np.ndarray, store_id: str,
                     current_ts: datetime) -> tuple[str | None, float]:
        """
        Search exit pool for a matching person.
        Returns (visitor_id, score) or (None, 0.0).
        """
        self._prune_exits(current_ts)
        best_id, best_score = None, 0.0
        for c in self._exits:
            if c.store_id != store_id:
                continue
            if c.embedding.shape != embedding.shape:
                continue
            score = cosine_similarity(embedding, c.embedding)
            if score >= SIMILARITY_THRESHOLD and score > best_score:
                best_score, best_id = score, c.visitor_id
        return best_id, best_score

    def find_cross_camera(
        self, embedding: np.ndarray, store_id: str,
        exclude_camera_id: str, current_ts: datetime
    ) -> tuple[str | None, float]:
        """
        Search active session pool for the same person on a DIFFERENT camera.
        Returns (visitor_id, score) or (None, 0.0).
        """
        best_id, best_score = None, 0.0
        for (sid, vid), c in self._session.items():
            if sid != store_id:
                continue
            if c.camera_id == exclude_camera_id:
                continue
            if c.embedding.shape != embedding.shape:
                continue
            score = cosine_similarity(embedding, c.embedding)
            if score >= CROSS_CAM_THRESHOLD and score > best_score:
                best_score, best_id = score, vid
        return best_id, best_score

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _prune_exits(self, now: datetime) -> None:
        cutoff = now - timedelta(minutes=REENTRY_WINDOW_MINUTES)
        self._exits = [e for e in self._exits if e.ts >= cutoff]
