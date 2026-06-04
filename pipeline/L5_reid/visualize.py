#!/usr/bin/env python3
"""
L5 visualizer — plays a camera video with Re-ID and staff overlays.

Left panel  : video with coloured bboxes
Right panel : staff crop thumbnails (updated as new staff are detected)

Colours:
  Green box  — customer (known visitor_id)
  Red box    — staff
  Yellow box — unknown / not yet classified

Banners:
  Purple "REENTRY" when a returning visitor is recognised
  Cyan   "CROSS-CAM LINK" when same person linked across cameras

Usage:
    python visualize.py \\
        --events  output/reid_events.jsonl \\
        --tracks  ../L2_Detect/output/STORE_STORE_1/CAM_ENTRY_01__CAM\\ 3\\ -\\ entry.jsonl \\
        --video   "../../L1_StoreLayout_selfannotate/input/Store 1/CAM 3 - entry.mp4" \\
        --crops   output/staff_crops/ \\
        --speed   1.0
"""

try:
    import cv2
except ImportError:
    import subprocess, sys as _sys
    subprocess.check_call([_sys.executable, "-m", "pip", "install", "opencv-python-headless"])
    import cv2

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

# ── Palette ───────────────────────────────────────────────────────────────────
_C_CUSTOMER  = (50,  220, 50)    # green
_C_STAFF     = (50,  50,  220)   # red
_C_UNKNOWN   = (50,  220, 220)   # yellow
_C_REENTRY   = (220, 50,  220)   # purple
_C_CROSSCAM  = (220, 220, 50)    # cyan
_C_HUD       = (220, 220, 220)
_C_BG        = (20,  20,  20)
_C_PANEL     = (35,  35,  35)

SIDEBAR_W    = 220   # width of staff crops panel
THUMB_H      = 110   # thumbnail height
BANNER_FRAMES = 80


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _load_jsonl(path: Path) -> list[dict]:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def _build_frame_ts_map(tracks_path: Path) -> dict[int, datetime]:
    m: dict[int, datetime] = {}
    with open(tracks_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if "frame" in rec and "timestamp" in rec:
                m[rec["frame"]] = _parse_ts(rec["timestamp"])
    return m


def _build_frame_track_map(tracks_path: Path) -> dict[int, list[dict]]:
    """frame_num → list of track records."""
    m: dict[int, list[dict]] = {}
    with open(tracks_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("frame_meta", {}).get("dropped", False):
                continue
            m[rec["frame"]] = rec.get("tracks", [])
    return m


# ── Drawing ───────────────────────────────────────────────────────────────────

def _draw_track(frame, track: dict, staff_set: set, visitor_map: dict) -> None:
    tid = track["track_id"]
    bbox = track.get("bbox", [])
    if len(bbox) < 4:
        return
    x1, y1, x2, y2 = [int(v) for v in bbox]
    is_staff = tid in staff_set
    vid = visitor_map.get(tid)

    colour = _C_STAFF if is_staff else (_C_CUSTOMER if vid else _C_UNKNOWN)
    label  = f"{'STAFF' if is_staff else (vid or '?')}  #{tid}"

    cv2.rectangle(frame, (x1, y1), (x2, y2), colour, 2)
    (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
    cv2.rectangle(frame, (x1, y1 - th - 6), (x1 + tw + 4, y1), colour, -1)
    cv2.putText(frame, label, (x1 + 2, y1 - 4),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (10, 10, 10), 1, cv2.LINE_AA)

    # Centroid dot
    cx = int((track.get("centroid") or [(x1+x2)//2])[0])
    cy = int((track.get("centroid") or [0, (y1+y2)//2])[1])
    cv2.circle(frame, (cx, cy), 3, (255, 255, 255), -1)


def _draw_banner(frame, text: str, sub: str, colour, frames_left: int) -> None:
    h, w = frame.shape[:2]
    alpha = min(1.0, frames_left / (BANNER_FRAMES * 0.25))
    bh = 52
    roi = frame[h - bh:h, 0:w]
    bg = roi.copy()
    bg[:] = _C_BG
    cv2.addWeighted(bg, 0.75 * alpha, roi, 1 - 0.75 * alpha, 0, roi)
    cv2.putText(frame, text, (16, h - bh + 24),
                cv2.FONT_HERSHEY_SIMPLEX, 0.9, colour, 2, cv2.LINE_AA)
    if sub:
        cv2.putText(frame, sub, (16, h - bh + 46),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, _C_HUD, 1, cv2.LINE_AA)


def _draw_hud(frame, frame_num: int, n_staff: int, n_visitors: int) -> None:
    cv2.putText(frame, f"frame {frame_num}", (10, 26),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, _C_HUD, 1, cv2.LINE_AA)
    cv2.putText(frame, f"visitors:{n_visitors}  staff:{n_staff}",
                (10, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.55, _C_HUD, 1, cv2.LINE_AA)


def _build_sidebar(crops: list[tuple], panel_h: int) -> np.ndarray:
    """crops: list of (label, bgr_image)"""
    panel = np.full((panel_h, SIDEBAR_W, 3), _C_PANEL, dtype=np.uint8)
    cv2.putText(panel, "STAFF CROPS", (8, 20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (180, 180, 180), 1)

    y = 28
    for label, img in crops[-((panel_h - 28) // (THUMB_H + 8)):]:
        if y + THUMB_H > panel_h:
            break
        tw = int(img.shape[1] * THUMB_H / img.shape[0])
        thumb = cv2.resize(img, (min(tw, SIDEBAR_W - 8), THUMB_H))
        th, tw2 = thumb.shape[:2]
        panel[y:y+th, (SIDEBAR_W-tw2)//2:(SIDEBAR_W-tw2)//2+tw2] = thumb
        cv2.putText(panel, label, (4, y + th + 12),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.38, (200, 200, 200), 1)
        y += th + 18
    return panel


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--events",  required=True, help="L5 reid_events.jsonl")
    ap.add_argument("--tracks",  required=True, help="L2 tracks JSONL (same camera)")
    ap.add_argument("--video",   required=True, help="Source video (.mp4)")
    ap.add_argument("--crops",   default=None,  help="Staff crops directory")
    ap.add_argument("--speed",   type=float, default=1.0)
    ap.add_argument("--out",     default=None, help="Save annotated video to file")
    args = ap.parse_args()

    events      = _load_jsonl(Path(args.events))
    frame_ts_map= _build_frame_ts_map(Path(args.tracks))
    frame_tracks= _build_frame_track_map(Path(args.tracks))

    cap = cv2.VideoCapture(args.video)
    if not cap.isOpened():
        print(f"[ERROR] Cannot open video: {args.video}")
        sys.exit(1)

    fps   = cap.get(cv2.CAP_PROP_FPS) or 30.0
    vw    = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    vh    = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    # Map events to frame numbers via closest timestamp
    clip_start = min(frame_ts_map.values()) if frame_ts_map else None

    def _ev_frame(ev: dict) -> int:
        ev_ts = _parse_ts(ev["timestamp"])
        if frame_ts_map:
            return min(frame_ts_map, key=lambda k: abs((frame_ts_map[k] - ev_ts).total_seconds()))
        if clip_start:
            return max(0, round((ev_ts - clip_start).total_seconds() * fps))
        return 0

    frame_events: dict[int, list[dict]] = {}
    for ev in events:
        fn = _ev_frame(ev)
        frame_events.setdefault(fn, []).append(ev)

    # Per-track state
    staff_set:   set[int] = set()
    visitor_map: dict[int, str] = {}    # track_id → visitor_id
    active_banners: list[dict] = []
    staff_crops_list: list[tuple] = []  # (label, bgr_image)

    # Pre-load existing crop files
    if args.crops:
        crops_dir = Path(args.crops)
        for p in sorted(crops_dir.glob("*.jpg"))[:10]:
            img = cv2.imread(str(p))
            if img is not None:
                staff_crops_list.append((p.stem[-12:], img))

    writer = None
    out_w  = vw + SIDEBAR_W
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        writer = cv2.VideoWriter(args.out, cv2.VideoWriter_fourcc(*"mp4v"), fps, (out_w, vh))
        print(f"Saving → {args.out}")
    else:
        cv2.namedWindow("L5 Re-ID", cv2.WINDOW_NORMAL)

    delay_ms = max(1, int(1000 / fps / args.speed))
    paused   = False
    frame_num = 0

    print(f"Video {vw}×{vh} @ {fps:.0f}fps  |  {len(events)} L5 events  |  SPACE=pause Q=quit")

    while True:
        if not paused:
            ret, frame = cap.read()
            if not ret:
                break

            # Process events for this frame
            for ev in frame_events.get(frame_num, []):
                etype = ev["event_type"]
                tid   = ev.get("metadata", {}).get("track_id")
                vid   = ev.get("visitor_id")

                if etype == "STAFF_FLAGGED":
                    if tid is not None:
                        staff_set.add(tid)
                    crop_rel = ev.get("metadata", {}).get("crop_path")
                    if crop_rel and args.crops:
                        cp = Path(args.crops) / Path(crop_rel).name
                        img = cv2.imread(str(cp))
                        if img is not None:
                            staff_crops_list.append((f"#{tid}", img))

                elif etype in ("ENTRY", "REENTRY", "VISITOR_LINKED"):
                    if tid is not None and vid:
                        visitor_map[tid] = vid
                    if etype == "REENTRY":
                        sim = ev.get("metadata", {}).get("similarity_score", 0)
                        active_banners.append({
                            "text": f"RE-ENTRY  {vid}",
                            "sub":  f"similarity: {sim:.2f}",
                            "colour": _C_REENTRY,
                            "frames_left": BANNER_FRAMES,
                        })
                    elif etype == "VISITOR_LINKED":
                        orig = ev.get("metadata", {}).get("original_visitor_id", vid)
                        active_banners.append({
                            "text": f"CROSS-CAM  {orig}",
                            "sub":  f"linked from another camera",
                            "colour": _C_CROSSCAM,
                            "frames_left": BANNER_FRAMES,
                        })

        # Draw bboxes from L2 tracks
        for track in frame_tracks.get(frame_num, []):
            _draw_track(frame, track, staff_set, visitor_map)

        # Banners
        for b in active_banners[-2:]:
            _draw_banner(frame, b["text"], b["sub"], b["colour"], b["frames_left"])
        for b in active_banners:
            b["frames_left"] -= 1
        active_banners = [b for b in active_banners if b["frames_left"] > 0]

        _draw_hud(frame, frame_num, len(staff_set), len(set(visitor_map.values())))

        # Sidebar
        sidebar = _build_sidebar(staff_crops_list, vh)
        canvas  = np.hstack([frame, sidebar])

        if writer:
            writer.write(canvas)
        else:
            cv2.imshow("L5 Re-ID", canvas)
            key = cv2.waitKey(delay_ms) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord(" "):
                paused = not paused

        if not paused:
            frame_num += 1

    cap.release()
    if writer:
        writer.release()
        print(f"Done → {args.out}")
    else:
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
