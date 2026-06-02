"""
Derive per-frame UTC timestamps from clip filename + frame number.

Supported filename patterns:
  CAM_ZONE_01_20260308_152730.mp4
  CAM_ZONE_01_2026-03-08T15-27-30.mp4
  20260308_152730_CAM_ZONE_01.mp4

If no pattern matches, caller should warn and substitute a known start time
via override_utc. Without a valid start time, POS correlation is meaningless.
"""

import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional


# (regex, parser) pairs tried in order — first match wins
_PATTERNS: list[tuple[str, callable]] = [
    # 20260308_152730  or  20260308T152730
    (
        r'(\d{8})[_T](\d{6})',
        lambda d, t: datetime(
            int(d[0:4]), int(d[4:6]), int(d[6:8]),
            int(t[0:2]), int(t[2:4]), int(t[4:6]),
            tzinfo=timezone.utc,
        ),
    ),
    # 2026-03-08_15-27-30  or  2026-03-08T15:27:30
    (
        r'(\d{4}-\d{2}-\d{2})[_T](\d{2}[:\-]\d{2}[:\-]\d{2})',
        lambda d, t: datetime.fromisoformat(
            f"{d}T{t.replace('-', ':')}+00:00"
        ),
    ),
]


def parse_clip_start(
    filepath: str,
    override_utc: Optional[str] = None,
) -> Optional[datetime]:
    """
    Return clip start as a UTC-aware datetime.

    Priority: override_utc string → filename pattern → None.
    None means the caller should warn; do not silently use a wrong timestamp.
    """
    if override_utc:
        try:
            return datetime.fromisoformat(
                override_utc.replace("Z", "+00:00")
            ).astimezone(timezone.utc)
        except ValueError:
            pass

    stem = Path(filepath).stem
    for pattern, parser in _PATTERNS:
        m = re.search(pattern, stem)
        if m:
            try:
                return parser(m.group(1), m.group(2))
            except (ValueError, IndexError):
                continue

    return None


def frame_timestamp(clip_start: datetime, frame_number: int, fps: float) -> str:
    """ISO-8601 UTC timestamp string for a specific frame number."""
    ts = clip_start + timedelta(seconds=frame_number / fps)
    ms = ts.microsecond // 1000
    return ts.strftime(f"%Y-%m-%dT%H:%M:%S.{ms:03d}Z")
