#!/usr/bin/env python3
"""
L3 Zone Assignment — Entry Point
==================================
Reads an L2 tracks JSONL and a store_layout.json, emits one zone_events JSONL.

Event types: ZONE_ENTER, ZONE_EXIT, ZONE_DWELL

Usage (Windows):
    python run_l3.py ^
        --tracks   "../L2_Detect/output/STORE_STORE_1/CAM_ZONE_01__CAM 1 - zone.jsonl" ^
        --layout   "../L1_StoreLayout_selfannotate/output/Store 1/store_layout.json" ^
        --output   "output/CAM_ZONE_01_zone_events.jsonl"

    # Process all JSONL files in a folder:
    python run_l3.py ^
        --tracks_dir "../L2_Detect/output/STORE_STORE_1/" ^
        --layout     "../L1_StoreLayout_selfannotate/output/Store 1/store_layout.json" ^
        --output_dir "output/"
"""

import argparse
import json
import logging
import sys
import uuid
from pathlib import Path

from zone_mapper    import build_camera_zone_index, get_zone_for_centroid
from dwell_tracker  import ms_between, dwell_intervals_crossed, DISAPPEAR_TIMEOUT_MS
from state_manager  import StateManager, ZONE_CHANGE_DEBOUNCE_FRAMES

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("L3")


# ── event builders ────────────────────────────────────────────────────────────

def _base_event(
    event_type: str,
    store_id:   str,
    camera_id:  str,
    track_id:   int,
    zone:       dict,
    timestamp:  str,
    dwell_ms:   float,
    confidence: float,
    session_seq: int,
) -> dict:
    return {
        "event_id":   str(uuid.uuid4()),
        "store_id":   store_id,
        "camera_id":  camera_id,
        "visitor_id": None,          # filled by L6
        "event_type": event_type,
        "timestamp":  timestamp,
        "zone_id":    zone["zone_id"],
        "dwell_ms":   int(dwell_ms),
        "is_staff":   None,          # filled by L5
        "confidence": round(confidence, 3),
        "metadata": {
            "queue_depth":   None,
            "sku_zone":      zone.get("zone_name", zone["zone_id"]),
            "is_revenue_zone": zone.get("is_revenue_zone", False),
            "session_seq":   session_seq,
            "track_id":      track_id,
        },
    }


def _make_enter(store_id, camera_id, track_id, zone, ts, conf, session_seq,
                queue_depth=None) -> dict:
    ev = _base_event("ZONE_ENTER", store_id, camera_id, track_id,
                     zone, ts, 0, conf, session_seq)
    ev["metadata"]["queue_depth"] = queue_depth
    return ev


def _make_exit(store_id, camera_id, track_id, zone, ts, dwell_ms,
               conf, session_seq) -> dict:
    return _base_event("ZONE_EXIT", store_id, camera_id, track_id,
                       zone, ts, dwell_ms, conf, session_seq)


def _make_dwell(store_id, camera_id, track_id, zone, ts, dwell_ms,
                conf, session_seq) -> dict:
    return _base_event("ZONE_DWELL", store_id, camera_id, track_id,
                       zone, ts, dwell_ms, conf, session_seq)


# ── per-file processor ────────────────────────────────────────────────────────

def process_tracks_file(
    tracks_path: str,
    zone_index:  dict[str, list[dict]],
    output_path: str,
) -> dict:
    """
    Stream through one tracks JSONL, emit zone events to output_path.
    Returns a summary dict.
    """
    state_mgr = StateManager()
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    n_enter = n_exit = n_dwell = n_frames = n_occluded_skipped = 0

    with (
        open(tracks_path, encoding="utf-8") as inf,
        open(output_path, "w", encoding="utf-8") as outf,
    ):
        def emit(event: dict) -> None:
            outf.write(json.dumps(event) + "\n")

        for raw in inf:
            raw = raw.strip()
            if not raw:
                continue
            record = json.loads(raw)

            # skip dropped frames — no reliable centroid data
            if record.get("frame_meta", {}).get("dropped", False):
                continue

            camera_id  = record["camera_id"]
            store_id   = record["store_id"]
            frame_ts   = record["timestamp"]
            n_frames  += 1

            camera_zones = zone_index.get(camera_id, [])
            if not camera_zones:
                # camera is not in layout or has no valid zones — just advance timestamps
                for t in record.get("tracks", []):
                    state = state_mgr.get(camera_id, t["track_id"])
                    state.last_seen_ts = frame_ts
                continue

            active_ids: set[int] = set()

            for t in record.get("tracks", []):
                track_id = t["track_id"]
                conf     = t.get("confidence", 0.0)
                cx, cy   = t["centroid"]

                # occluded → skip zone assignment entirely for this frame
                if t.get("occluded", False):
                    n_occluded_skipped += 1
                    # still mark as seen so we don't falsely emit ZONE_EXIT
                    if state_mgr.has(camera_id, track_id):
                        state_mgr.get(camera_id, track_id).last_seen_ts = frame_ts
                    active_ids.add(track_id)
                    continue

                active_ids.add(track_id)
                state = state_mgr.get(camera_id, track_id)
                state.last_confidence = conf

                # time elapsed since this track was last seen
                elapsed_ms = (
                    ms_between(state.last_seen_ts, frame_ts)
                    if state.last_seen_ts else 0.0
                )

                zone        = get_zone_for_centroid(cx, cy, camera_zones)
                new_zone_id = zone["zone_id"] if zone else None

                # ── debounce: buffer tentative zone changes ────────────────────
                # A zone change is only committed once the centroid has been in
                # the new zone for ZONE_CHANGE_DEBOUNCE_FRAMES consecutive frames.
                # This prevents a single jitter frame at a polygon edge from
                # generating a spurious EXIT+ENTER pair.
                if new_zone_id != state.current_zone_id:
                    if new_zone_id == state.pending_zone_id:
                        state.pending_frames += 1
                    else:
                        # new candidate — reset debounce counter
                        state.pending_zone_id = new_zone_id
                        state.pending_zone    = zone
                        state.pending_frames  = 1

                    if state.pending_frames >= ZONE_CHANGE_DEBOUNCE_FRAMES:
                        # transition confirmed — commit it
                        committed_zone = state.pending_zone
                        state.pending_zone_id = None
                        state.pending_zone    = None
                        state.pending_frames  = 0

                        # exit old zone
                        if state.current_zone_id is not None:
                            old_stub = {
                                "zone_id":         state.current_zone_id,
                                "zone_name":       state.current_zone_name,
                                "zone_type":       state.current_zone_type,
                                "is_revenue_zone": state.is_revenue_zone,
                            }
                            emit(_make_exit(
                                store_id, camera_id, track_id,
                                old_stub, frame_ts,
                                state.total_dwell_ms + elapsed_ms,
                                conf, state.session_seq,
                            ))
                            n_exit += 1

                        # enter new zone
                        if committed_zone is not None:
                            queue_depth = None
                            if committed_zone.get("zone_type") == "billing":
                                queue_depth = state_mgr.billing_queue_depth(
                                    camera_id, track_id
                                )
                            state_mgr.enter_zone(camera_id, track_id, committed_zone, frame_ts)
                            emit(_make_enter(
                                store_id, camera_id, track_id,
                                committed_zone, frame_ts, conf, state.session_seq,
                                queue_depth=queue_depth,
                            ))
                            n_enter += 1
                        else:
                            state_mgr.exit_zone(camera_id, track_id)

                    # still in debounce window — accumulate dwell in current zone
                    # (don't update dwell here; handled below for confirmed zone)

                else:
                    # same confirmed zone — reset any stale pending debounce
                    state.pending_zone_id = None
                    state.pending_zone    = None
                    state.pending_frames  = 0

                    # ── accumulate dwell ──────────────────────────────────────
                    if state.current_zone_id is not None:
                        crossings = dwell_intervals_crossed(
                            state.total_dwell_ms,
                            state.last_dwell_emit_ms,
                            elapsed_ms,
                        )
                        state.total_dwell_ms += elapsed_ms
                        for dwell_val in crossings:
                            state.last_dwell_emit_ms = dwell_val
                            emit(_make_dwell(
                                store_id, camera_id, track_id,
                                zone, frame_ts, dwell_val,
                                conf, state.session_seq,
                            ))
                            n_dwell += 1
                    else:
                        state.floor_time_ms += elapsed_ms

                state.last_seen_ts = frame_ts

            # ── disappearance check ───────────────────────────────────────────
            # Any track in state for this camera that wasn't active this frame
            # and hasn't been seen for > DISAPPEAR_TIMEOUT_MS → emit ZONE_EXIT.
            for (cam, tid) in state_mgr.all_keys():
                if cam != camera_id or tid in active_ids:
                    continue
                st = state_mgr.get(cam, tid)
                if not st.last_seen_ts:
                    continue
                gap_ms = ms_between(st.last_seen_ts, frame_ts)
                if gap_ms >= DISAPPEAR_TIMEOUT_MS and st.current_zone_id is not None:
                    old_zone_stub = {
                        "zone_id":         st.current_zone_id,
                        "zone_name":       st.current_zone_name,
                        "zone_type":       st.current_zone_type,
                        "is_revenue_zone": st.is_revenue_zone,
                    }
                    emit(_make_exit(
                        store_id, camera_id, tid,
                        old_zone_stub, frame_ts,   # detected-at, not last-seen-at
                        st.total_dwell_ms, st.last_confidence, st.session_seq,
                    ))
                    n_exit += 1
                    state_mgr.exit_zone(cam, tid)

                # purge the track entirely if gone for > 2 × timeout
                if gap_ms >= DISAPPEAR_TIMEOUT_MS * 2:
                    state_mgr.remove(cam, tid)

    return {
        "tracks_file": tracks_path,
        "output":      output_path,
        "frames_read": n_frames,
        "occluded_skipped": n_occluded_skipped,
        "events": {"ZONE_ENTER": n_enter, "ZONE_EXIT": n_exit, "ZONE_DWELL": n_dwell},
    }


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="L3 Zone Assignment")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--tracks",     help="Single L2 tracks .jsonl file")
    g.add_argument("--tracks_dir", help="Directory of L2 .jsonl files (all processed)")

    ap.add_argument("--layout", required=True,
                    help="store_layout.json path (from L1 output)")

    o = ap.add_mutually_exclusive_group()
    o.add_argument("--output",     default=None, help="Output .jsonl path (single-file mode)")
    o.add_argument("--output_dir", default="output",
                   help="Output directory (directory mode, default: output/)")

    args = ap.parse_args()

    with open(args.layout, encoding="utf-8") as f:
        layout = json.load(f)

    zone_index = build_camera_zone_index(layout)
    logger.info(
        "Layout loaded — %d cameras, zones per camera: %s",
        len(zone_index),
        {cam: len(zones) for cam, zones in zone_index.items()},
    )

    # collect (tracks_path, output_path) pairs
    pairs: list[tuple[str, str]] = []

    if args.tracks:
        out = args.output or str(
            Path("output") / (Path(args.tracks).stem + "_zone_events.jsonl")
        )
        pairs.append((args.tracks, out))
    else:
        out_dir = Path(args.output_dir)
        for p in sorted(Path(args.tracks_dir).glob("*.jsonl")):
            out = out_dir / (p.stem + "_zone_events.jsonl")
            pairs.append((str(p), str(out)))

    if not pairs:
        logger.error("No .jsonl files found.")
        sys.exit(1)

    total_events = {"ZONE_ENTER": 0, "ZONE_EXIT": 0, "ZONE_DWELL": 0}

    for tracks_path, output_path in pairs:
        logger.info("Processing  %s", Path(tracks_path).name)
        summary = process_tracks_file(tracks_path, zone_index, output_path)
        for k in total_events:
            total_events[k] += summary["events"][k]
        logger.info(
            "  → %d frames | %d occluded skipped | "
            "ENTER=%d  EXIT=%d  DWELL=%d  →  %s",
            summary["frames_read"],
            summary["occluded_skipped"],
            summary["events"]["ZONE_ENTER"],
            summary["events"]["ZONE_EXIT"],
            summary["events"]["ZONE_DWELL"],
            Path(output_path).name,
        )

    logger.info(
        "All done — total events: ENTER=%d  EXIT=%d  DWELL=%d",
        total_events["ZONE_ENTER"],
        total_events["ZONE_EXIT"],
        total_events["ZONE_DWELL"],
    )


if __name__ == "__main__":
    main()
