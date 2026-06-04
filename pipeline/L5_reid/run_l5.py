#!/usr/bin/env python3
"""
L5 — Staff Detection + Re-ID
=============================
Reads L2 tracks (all cameras) + L4 entry events.
For every tracked person:
  • Extracts a bbox crop from the source video
  • Decides if they are staff (HSV → Groq VLM fallback)
  • Saves crop to output/staff_crops/ for staff detections
  • Runs Re-ID: checks if this person was seen before (re-entry)
    or is already active on another camera (cross-camera linking)

Outputs reid_events.jsonl — one JSON line per event:
  ENTRY · EXIT · REENTRY · VISITOR_LINKED · STAFF_FLAGGED

Multi-camera note:
  Entry cameras are processed first so their embeddings populate the
  session pool.  Zone cameras are then processed and cross-camera
  matches link their track IDs to existing visitor IDs.

Usage:
    python run_l5.py \\
        --tracks_dir  ../L2_Detect/output/STORE_STORE_1/ \\
        --entry_events ../L4_entry/output/store1_entry_events.jsonl \\
        --videos_dir  ../L1_StoreLayout_selfannotate/input/Store\\ 1/ \\
        --layout      ../L1_StoreLayout_selfannotate/output/Store\\ 1/store_layout.json \\
        --output_dir  output/
"""

import argparse
import json
import sys
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from embedder import extract_embedding
from staff_detector import StaffDetector
from vlm_fallback import groq_staff_check
from reid_cache import ReIDCache
from reentry_handler import ReentryHandler
from person_verifier import is_person


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))

def _fmt_ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"

def _load_jsonl(path: Path) -> list[dict]:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out

def _find_video(videos_dir: Path, camera_id: str) -> Path | None:
    """Match camera_id to a video file by scanning the videos directory."""
    if not videos_dir or not videos_dir.is_dir():
        return None
    for ext in ("mp4", "mov", "mkv", "avi"):
        for f in videos_dir.glob(f"*.{ext}"):
            stem_low = f.stem.lower()
            cam_low = camera_id.lower()
            # CAM_ENTRY_01 should match "entry 1" or "entry1" etc.
            if cam_low.replace("_", " ") in stem_low or cam_low in stem_low:
                return f
            # Try matching by type keyword + number
            parts = cam_low.split("_")
            if len(parts) >= 2:
                kind = parts[1]          # "entry", "zone", "billing"
                num  = parts[-1].lstrip("0") or "1"
                if kind in stem_low and num in stem_low:
                    return f
    return None


def _crop_from_frame(frame: np.ndarray, bbox: list) -> np.ndarray:
    x1, y1, x2, y2 = [int(v) for v in bbox]
    h, w = frame.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 <= x1 or y2 <= y1:
        return np.zeros((1, 1, 3), dtype=np.uint8)
    return frame[y1:y2, x1:x2].copy()


def _make_event(event_type: str, store_id: str, camera_id: str,
                visitor_id: str, ts: datetime, track_id: int,
                confidence: float, is_staff: bool | None,
                extra: dict | None = None) -> dict:
    ev = {
        "event_id":   str(uuid.uuid4()),
        "store_id":   store_id,
        "camera_id":  camera_id,
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp":  _fmt_ts(ts),
        "is_staff":   is_staff,
        "confidence": round(confidence, 4),
        "metadata":   {"track_id": track_id, **(extra or {})},
    }
    return ev


# ── Per-camera processor ──────────────────────────────────────────────────────

def process_camera(
    tracks_path: Path,
    video_path: Path | None,
    layout: dict,
    store_id: str,
    entry_events_by_track: dict,    # track_id → L4 event dict
    staff_detector: StaffDetector,
    reid_handler: ReentryHandler,
    crops_dir: Path,
    out_f,
) -> int:
    """Process one camera's L2 tracks file. Returns event count."""
    records = _load_jsonl(tracks_path)
    if not records:
        return 0

    # Determine camera_id from first non-dropped record
    camera_id = next(
        (r["camera_id"] for r in records
         if not r.get("frame_meta", {}).get("dropped", False)), None
    )
    if not camera_id:
        return 0

    print(f"  [{camera_id}]  {tracks_path.name}")

    # Open video (optional — crops are skipped if no video)
    cap = None
    if video_path and video_path.exists():
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            print(f"    [WARN] Cannot open video {video_path.name} — skipping crops")
            cap = None

    seen_tracks: set[int] = set()
    current_frame_img: np.ndarray | None = None
    current_video_frame = -1
    event_count = 0

    for rec in records:
        if rec.get("frame_meta", {}).get("dropped", False):
            continue

        frame_num = rec["frame"]
        try:
            frame_ts = _parse_ts(rec["timestamp"])
        except Exception:
            continue

        # Seek video to this frame
        if cap is not None and frame_num != current_video_frame:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_num)
            ret, current_frame_img = cap.read()
            current_video_frame = frame_num if ret else -1

        for track in rec.get("tracks", []):
            track_id: int = track["track_id"]
            bbox      = track.get("bbox", [0, 0, 0, 0])
            confidence= track.get("confidence", 0.0)
            is_new    = track_id not in seen_tracks
            seen_tracks.add(track_id)

            # Extract crop
            crop = None
            if current_frame_img is not None:
                crop = _crop_from_frame(current_frame_img, bbox)

            # Reject non-person crops (bags, chairs, etc. misclassified by ByteTrack)
            if crop is not None and not is_person(crop):
                continue

            # ── Staff detection ───────────────────────────────────────────
            is_staff, method, s_conf = staff_detector.check(
                camera_id, track_id, crop,
                {"long_duration_flag": track.get("long_duration_flag", False)},
                frame_ts,
            )

            # VLM fallback for ambiguous result
            if is_staff is None and crop is not None:
                vlm_result, vlm_conf = groq_staff_check(crop)
                staff_detector.apply_vlm_result(camera_id, track_id, vlm_result, vlm_conf)
                is_staff, method, s_conf = vlm_result, "vlm_groq", vlm_conf

            # Save crop if staff (once per track, on first detection)
            if is_staff and is_new and crop is not None and crop.size > 0:
                crop_path = crops_dir / f"{store_id}_{camera_id}_{track_id}_{frame_num}.jpg"
                cv2.imwrite(str(crop_path), crop)
                ev = _make_event(
                    "STAFF_FLAGGED", store_id, camera_id,
                    f"STAFF_{camera_id}_{track_id}", frame_ts,
                    track_id, s_conf, True,
                    {"detection_method": method,
                     "crop_path": str(crop_path.relative_to(crops_dir.parent))},
                )
                out_f.write(json.dumps(ev) + "\n")
                event_count += 1
                print(f"    Staff flagged: track {track_id}  method={method}")

            # Staff skip Re-ID
            if is_staff:
                continue

            # ── Re-ID / cross-camera linking ──────────────────────────────
            if not is_new:
                continue  # only process first appearance

            embedding = extract_embedding(crop) if crop is not None else None

            # Get visitor_id from L4 if this is an entry camera event
            l4_event = entry_events_by_track.get((camera_id, track_id))
            if l4_event:
                visitor_id = l4_event["visitor_id"]
            else:
                visitor_id = f"VIS_ZONE_{camera_id}_{track_id}"

            if confidence < 0.4:
                # Too blurry for reliable Re-ID — skip, just register
                reid_handler._track_map[(camera_id, track_id)] = visitor_id
                continue

            reid_result = reid_handler.on_entry(
                visitor_id, track_id, camera_id, embedding, frame_ts
            )

            etype = reid_result["event_type"]
            final_visitor = reid_result["visitor_id"]
            sim = reid_result.get("similarity_score")

            extra = {"track_id": track_id}
            if etype == "REENTRY":
                extra["similarity_score"]   = sim
                extra["new_visitor_id"]     = reid_result.get("new_visitor_id")
                extra["reentry_gap_minutes"] = None  # downstream can compute from events
            elif etype == "VISITOR_LINKED":
                extra["similarity_score"]   = sim
                extra["linked_camera_id"]   = camera_id
                extra["original_visitor_id"] = reid_result.get("original_visitor_id")

            ev = _make_event(etype, store_id, camera_id, final_visitor,
                             frame_ts, track_id, confidence, False, extra)
            out_f.write(json.dumps(ev) + "\n")
            event_count += 1

    if cap:
        cap.release()
    return event_count


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tracks_dir",    required=True, help="L2 output folder (all cameras)")
    ap.add_argument("--entry_events",  required=True, help="L4 entry_events.jsonl")
    ap.add_argument("--videos_dir",    default=None,  help="Folder containing source videos")
    ap.add_argument("--layout",        required=True, help="store_layout.json")
    ap.add_argument("--store_id",      default=None)
    ap.add_argument("--output_dir",    default="output")
    args = ap.parse_args()

    with open(args.layout, encoding="utf-8") as f:
        layout = json.load(f)

    store_id   = args.store_id or layout.get("store_id", "STORE_UNKNOWN")
    boh_zones  = layout.get("boh_zones", [])
    tracks_dir = Path(args.tracks_dir)
    out_dir    = Path(args.output_dir)
    crops_dir  = out_dir / "staff_crops"
    out_dir.mkdir(parents=True, exist_ok=True)
    crops_dir.mkdir(parents=True, exist_ok=True)
    videos_dir = Path(args.videos_dir) if args.videos_dir else None

    # Load L4 events → index by (camera_id, track_id) for quick lookup
    entry_events_by_track: dict[tuple, dict] = {}
    for ev in _load_jsonl(Path(args.entry_events)):
        tid = ev.get("metadata", {}).get("track_id")
        cam = ev.get("camera_id")
        if tid is not None and cam:
            entry_events_by_track[(cam, tid)] = ev

    print(f"Loaded {len(entry_events_by_track)} L4 entry events")

    staff_detector = StaffDetector(boh_zones=boh_zones)
    reid_cache     = ReIDCache()
    reid_handler   = ReentryHandler(store_id, reid_cache)

    # Sort: entry cameras first so their embeddings seed the session pool
    all_jsonl = sorted(tracks_dir.glob("*.jsonl"))
    entry_files = [p for p in all_jsonl if "ENTRY" in p.stem.upper()]
    other_files = [p for p in all_jsonl if "ENTRY" not in p.stem.upper()]
    ordered     = entry_files + other_files

    out_path    = out_dir / "reid_events.jsonl"
    total_events = 0

    with open(out_path, "w", encoding="utf-8") as out_f:
        for tp in ordered:
            vp = _find_video(videos_dir, tp.stem.split("__")[0]) if videos_dir else None
            n = process_camera(
                tp, vp, layout, store_id,
                entry_events_by_track,
                staff_detector, reid_handler,
                crops_dir, out_f,
            )
            total_events += n
            print(f"    → {n} events")

    print(f"\n✅  L5 complete — {total_events} events → {out_path}")
    print(f"   Staff crops  → {crops_dir}")


if __name__ == "__main__":
    main()
