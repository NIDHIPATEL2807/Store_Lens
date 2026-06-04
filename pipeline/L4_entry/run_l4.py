#!/usr/bin/env python3
"""
L4 Entry/Exit Detection — zone-transition based
=================================================
Reads L3 zone_events.jsonl for entry cameras and watches for transitions
between OUTSIDE-* and INSIDE-* zones to emit ENTRY / EXIT events.

Zone naming convention (set during L1 annotation):
  OUTSIDE-*  →  area outside the store door
  INSIDE-*   →  area just inside the store
  ENTRY      →  the door / threshold itself (treated as neutral)

Logic:
  OUTSIDE → INSIDE  =  ENTRY  (customer walked in)
  INSIDE  → OUTSIDE =  EXIT   (customer walked out)
  OUTSIDE only      =  ignore (person walking past outside)
  Born-inside       =  ENTRY  (track appeared first time in INSIDE zone)

Usage (single file):
    python run_l4.py \\
        --zone_events  ../L3_ZoneAssign/output/CAM_ENTRY_01_zone_events.jsonl \\
        --layout       ../L1_StoreLayout_selfannotate/output/Store\\ 1/store_layout.json \\
        --output       output/store1_entry_events.jsonl

Usage (batch — all .jsonl in a folder):
    python run_l4.py \\
        --zone_events_dir ../L3_ZoneAssign/output/ \\
        --layout          ../L1_StoreLayout_selfannotate/output/Store\\ 1/store_layout.json \\
        --output_dir      output/
"""

import argparse
import json
import sys
import uuid
from datetime import datetime
from pathlib import Path

from visitor_id import generate_visitor_id

REENTRY_SUPPRESSION_SECONDS = 30
GROUP_WINDOW_SECONDS        = 4.0   # entries within this window = same group


# ── Group pre-computation (batch pass before main loop) ───────────────────────

def _precompute_groups(zone_events_path: Path) -> dict[int, tuple[str | None, int]]:
    """
    First pass over the file: collect every ZONE_ENTER(INSIDE) timestamp,
    cluster them by time, assign group_ids to ALL members upfront.

    Returns {track_id: (group_id, group_size)}
    group_id is None for solo entries.
    """
    entries: list[tuple[datetime, int]] = []

    with open(zone_events_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("event_type") != "ZONE_ENTER":
                continue
            if _zone_side(rec.get("zone_id", "") or "") != "inside":
                continue
            tid = rec.get("metadata", {}).get("track_id")
            if tid is None:
                continue
            try:
                entries.append((_parse_ts(rec["timestamp"]), tid))
            except Exception:
                pass

    entries.sort(key=lambda x: x[0])

    group_map: dict[int, tuple[str | None, int]] = {}
    processed: set[int] = set()

    for i, (ts_i, tid_i) in enumerate(entries):
        if tid_i in processed:
            continue
        # Collect everyone within GROUP_WINDOW_SECONDS of this entry
        members = [(ts_i, tid_i)]
        for j in range(i + 1, len(entries)):
            ts_j, tid_j = entries[j]
            if (ts_j - ts_i).total_seconds() <= GROUP_WINDOW_SECONDS:
                members.append((ts_j, tid_j))
            else:
                break

        if len(members) > 1:
            gid   = f"G_{uuid.uuid4().hex[:6]}"
            gsize = len(members)
            for _, tid in members:
                group_map[tid] = (gid, gsize)
                processed.add(tid)
        else:
            group_map[tid_i] = (None, 1)
            processed.add(tid_i)

    groups_found = sum(1 for v in group_map.values() if v[0] is not None)
    print(f"    [groups] {len(entries)} entries → {groups_found} in groups")


# ── Zone classification ───────────────────────────────────────────────────────

def _zone_side(zone_id: str) -> str | None:
    """
    Returns 'outside', 'inside', 'door', or None.
    Matches the naming convention used in L1 annotation.
    """
    if not zone_id:
        return None
    zid = zone_id.upper()
    if zid.startswith("OUTSIDE"):
        return "outside"
    if zid.startswith("INSIDE"):
        return "inside"
    if zid in ("ENTRY", "DOOR", "THRESHOLD"):
        return "door"
    return None


# ── Timestamp helpers ─────────────────────────────────────────────────────────

def _parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _fmt_ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _clip_date_from_events(path: Path) -> str:
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rec = json.loads(line)
                ts = rec.get("timestamp", "")
                if ts:
                    return ts[:10]
    return "unknown"


# ── Event builder ─────────────────────────────────────────────────────────────

def _make_event(
    event_type: str,
    store_id: str,
    camera_id: str,
    visitor_id: str,
    ts: datetime,
    track_id: int,
    confidence: float,
    dwell_ms: float,
    session_seq: int,
    group_id: str | None,
    group_size: int,
) -> dict:
    return {
        "event_id":   str(uuid.uuid4()),
        "store_id":   store_id,
        "camera_id":  camera_id,
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp":  _fmt_ts(ts),
        "zone_id":    "ENTRY_ZONE",
        "dwell_ms":   int(dwell_ms),
        "is_staff":   None,
        "confidence": round(confidence, 4),
        "metadata": {
            "queue_depth":  None,
            "sku_zone":     None,
            "session_seq":  session_seq,
            "group_id":     group_id,
            "group_size":   group_size,
            "track_id":     track_id,
        },
    }


# ── Core processor ────────────────────────────────────────────────────────────

def process_zone_events(
    zone_events_path: Path,
    layout: dict,
    store_id: str,
    clip_date: str,
    out_f,
) -> int:
    """
    Read L3 zone events for one entry camera and emit ENTRY/EXIT events.
    Returns number of events emitted.
    """
    # Verify this is an entry camera file
    entry_cam_ids = {
        cam["camera_id"]
        for cam in layout.get("cameras", [])
        if cam.get("camera_type") == "entry"
    }

    camera_id = None
    with open(zone_events_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rec = json.loads(line)
                camera_id = rec.get("camera_id")
                break

    if camera_id is None:
        print(f"  [SKIP] Empty file: {zone_events_path.name}")
        return 0

    if camera_id not in entry_cam_ids:
        print(f"  [SKIP] {camera_id} is not an entry camera")
        return 0

    print(f"  {zone_events_path.name}  →  camera {camera_id}")

    # Pre-compute group assignments — all members get group_id before any event is written
    group_map = _precompute_groups(zone_events_path)

    # Per-track state: track_id → {side, visitor_id, entry_ts, session_seq, last_entry_ts}
    track_state: dict[int, dict] = {}
    event_count = 0

    with open(zone_events_path, encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            rec = json.loads(raw)

            etype      = rec.get("event_type", "")
            zone_id    = rec.get("zone_id", "") or ""
            side       = _zone_side(zone_id)
            track_id   = rec.get("metadata", {}).get("track_id")
            confidence = rec.get("confidence", 0.0)

            if track_id is None or side is None:
                continue

            try:
                frame_ts = _parse_ts(rec["timestamp"])
            except Exception:
                continue

            # ── Only ZONE_ENTER drives the state machine ──────────────────────
            # ZONE_EXIT from INSIDE means person walked DEEPER into the store —
            # that is NOT an exit. We never emit EXIT on ZONE_EXIT(INSIDE).
            # EXIT only fires when the person comes back through the door
            # (INSIDE → DOOR → OUTSIDE sequence via ZONE_ENTER events).
            if etype != "ZONE_ENTER":
                continue

            st       = track_state.get(track_id)
            prev     = st["side"] if st else None
            status   = st["status"] if st else "outside_store"

            # ── First appearance of this track ────────────────────────────────
            if st is None:
                if side == "inside":
                    # Born-inside: track appeared already past the door
                    visitor_id = generate_visitor_id(track_id, store_id, clip_date)
                    group_id, group_size = group_map.get(track_id, (None, 1))
                    seq = 1
                    out_f.write(json.dumps(_make_event(
                        "ENTRY", store_id, camera_id, visitor_id,
                        frame_ts, track_id, confidence, 0, seq, group_id, group_size,
                    )) + "\n")
                    event_count += 1
                    track_state[track_id] = {
                        "side": "inside", "status": "inside_store",
                        "visitor_id": visitor_id, "entry_ts": frame_ts,
                        "session_seq": seq, "last_entry_ts": frame_ts,
                    }
                else:
                    track_state[track_id] = {
                        "side": side, "status": "outside_store",
                        "visitor_id": None, "entry_ts": None,
                        "session_seq": 0, "last_entry_ts": None,
                    }
                continue

            # ── ENTRY: person crossed into INSIDE zone ────────────────────────
            # Valid paths: outside→inside  or  door→inside
            # (person just walked through the door into the store)
            if side == "inside" and prev in ("outside", "door") and status == "outside_store":
                last_entry = st.get("last_entry_ts")
                if last_entry and (frame_ts - last_entry).total_seconds() < REENTRY_SUPPRESSION_SECONDS:
                    st["side"] = side
                    continue
                if st.get("visitor_id") is None:
                    st["visitor_id"] = generate_visitor_id(track_id, store_id, clip_date)
                group_id, group_size = group_map.get(track_id, (None, 1))
                st["session_seq"]   += 1
                st["entry_ts"]       = frame_ts
                st["last_entry_ts"]  = frame_ts
                st["status"]         = "inside_store"
                out_f.write(json.dumps(_make_event(
                    "ENTRY", store_id, camera_id, st["visitor_id"],
                    frame_ts, track_id, confidence, 0,
                    st["session_seq"], group_id, group_size,
                )) + "\n")
                event_count += 1

            # ── Moving through door on the way out ────────────────────────────
            # inside → door: mark as "exiting" so we know they're heading out
            elif side == "door" and prev == "inside" and status == "inside_store":
                st["status"] = "exiting"

            # ── EXIT: person went back through the door to outside ────────────
            # Valid paths: door→outside  (came back through ENTRY gate)
            #              inside→outside (no dedicated ENTRY zone annotated)
            # Both require status == inside_store or exiting.
            elif side == "outside" and status in ("inside_store", "exiting"):
                entry_ts = st.get("entry_ts")
                dm = (frame_ts - entry_ts).total_seconds() * 1000 if entry_ts else 0
                st["session_seq"] += 1
                st["status"]       = "outside_store"
                out_f.write(json.dumps(_make_event(
                    "EXIT", store_id, camera_id, st["visitor_id"] or "UNKNOWN",
                    frame_ts, track_id, confidence, dm, st["session_seq"], None, 1,
                )) + "\n")
                event_count += 1

            # ── Person moved deeper into store (INSIDE → other / off-frame) ──
            # Do nothing. They're still inside. We wait for them to come back.

            # Update last-seen side
            st["side"] = side

    return event_count


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zone_events",     help="Single L3 zone_events.jsonl for an entry camera")
    ap.add_argument("--zone_events_dir", help="Batch: process all .jsonl in this directory")
    ap.add_argument("--layout",          required=True)
    ap.add_argument("--store_id",        default=None)
    ap.add_argument("--clip_date",       default=None, help="YYYY-MM-DD for visitor ID hashing")
    ap.add_argument("--output",          default=None)
    ap.add_argument("--output_dir",      default="output")
    args = ap.parse_args()

    if not args.zone_events and not args.zone_events_dir:
        print("[ERROR] Provide --zone_events or --zone_events_dir")
        sys.exit(1)

    with open(args.layout, encoding="utf-8") as f:
        layout = json.load(f)

    store_id = args.store_id or layout.get("store_id", "STORE_UNKNOWN")

    if args.zone_events:
        files = [Path(args.zone_events)]
    else:
        files = sorted(Path(args.zone_events_dir).glob("*.jsonl"))
        if not files:
            print(f"[ERROR] No .jsonl files in {args.zone_events_dir}")
            sys.exit(1)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    total = 0
    for zp in files:
        clip_date = args.clip_date or _clip_date_from_events(zp)

        if args.output and args.zone_events:
            out_path = Path(args.output)
            out_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            out_path = out_dir / f"{zp.stem}_entry_events.jsonl"

        with open(out_path, "w", encoding="utf-8") as out_f:
            n = process_zone_events(zp, layout, store_id, clip_date, out_f)
            total += n
            print(f"  → {out_path.name}  ({n} events)")

    print(f"\n✅  L4 complete — {total} total events across {len(files)} file(s)")


if __name__ == "__main__":
    main()
