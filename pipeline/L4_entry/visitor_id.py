"""
Deterministic visitor ID generator.

Same track_id on the same day always produces the same VIS_ token,
making re-processing idempotent.
"""

import hashlib


def generate_visitor_id(track_id: int, store_id: str, clip_date: str) -> str:
    raw = f"{track_id}_{store_id}_{clip_date}"
    h = hashlib.sha256(raw.encode()).hexdigest()[:6]
    return f"VIS_{h}"
