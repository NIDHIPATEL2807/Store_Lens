"""
Dwell timing helpers — isolated so they can be tested without I/O.
"""

from datetime import datetime, timezone

DWELL_EMIT_INTERVAL_MS: float = 30_000.0   # emit ZONE_DWELL every 30 s of continuous presence
DISAPPEAR_TIMEOUT_MS:   float =  2_000.0   # emit ZONE_EXIT after 2 s without seeing a track


def parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)


def ms_between(ts_earlier: str, ts_later: str) -> float:
    """Milliseconds elapsed between two ISO-8601 UTC timestamp strings. Returns 0 on error."""
    try:
        return (parse_ts(ts_later) - parse_ts(ts_earlier)).total_seconds() * 1000.0
    except (ValueError, OverflowError):
        return 0.0


def dwell_intervals_crossed(
    total_dwell_ms: float,
    last_dwell_emit_ms: float,
    elapsed_ms: float,
) -> list[float]:
    """
    Return a list of dwell_ms values for each 30-second threshold crossed
    in the interval [last_dwell_emit_ms, total_dwell_ms + elapsed_ms].

    Usually this list has 0 or 1 entries. It can have 2+ if a frame was
    delayed (e.g. a clip resumed after a long gap) but that is handled
    correctly — one ZONE_DWELL per crossing.
    """
    new_total = total_dwell_ms + elapsed_ms
    crossings: list[float] = []
    next_threshold = last_dwell_emit_ms + DWELL_EMIT_INTERVAL_MS
    while next_threshold <= new_total:
        crossings.append(next_threshold)
        next_threshold += DWELL_EMIT_INTERVAL_MS
    return crossings
