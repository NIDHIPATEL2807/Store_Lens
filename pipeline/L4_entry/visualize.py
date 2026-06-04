#!/usr/bin/env python3
"""
L4 visualizer — plays the entry-camera video with entry/exit events overlaid.

What you see:
  • Entry threshold line (orange) drawn on every frame
  • Green banner  "ENTRY  VIS_xxxxxx  group:2"  when someone enters
  • Red   banner  "EXIT   VIS_xxxxxx  dwell: 4m12s"  when someone exits
  • Top-right counter: "Inside: N"
  • Press SPACE to pause, Q to quit

Usage:
    python visualize.py \\
        --events output/CAM_ENTRY_01__CAM 3 - entry_entry_events.jsonl \\
        --video  "../../L1_StoreLayout_selfannotate/input/Store 1/CAM 3 - entry.mp4" \\
        --layout "../../L1_StoreLayout_selfannotate/output/Store 1/store_layout.json" \\
        --tracks "../../L2_Detect/output/STORE_STORE_1/CAM_ENTRY_01__CAM 3 - entry.jsonl"

    # Save annotated video instead of live window
    python visualize.py --events ... --video ... --layout ... --tracks ... --out review.mp4

    # Play at 2× speed
    python visualize.py --events ... --video ... --layout ... --tracks ... --speed 2.0
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

# ── BGR palette ───────────────────────────────────────────────────────────────
_C_ENTRY   = (50,  220, 50)    # green
_C_EXIT    = (50,  50,  220)   # red
_C_LINE    = (0,   200, 255)   # orange
_C_HUD     = (220, 220, 220)   # light grey
_C_BG      = (20,  20,  20)

BANNER_FRAMES = 90  # frames to keep a banner on screen (~3s at 30fps)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _fmt_dwell(ms: float) -> str:
    s = int(ms / 1000)
    return f"{s//60}m{s%60:02d}s" if s >= 60 else f"{s}s"


def _load_events(path: Path) -> list[dict]:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    out.sort(key=lambda e: e["timestamp"])
    return out


def _build_frame_ts_map(tracks_path: Path) -> dict[int, datetime]:
    """Maps frame_num → timestamp from an L2 tracks JSONL file."""
    fmap: dict[int, datetime] = {}
    with open(tracks_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if "frame" in rec and "timestamp" in rec:
                fmap[rec["frame"]] = _parse_ts(rec["timestamp"])
    return fmap


def _get_entry_cam(layout: dict, camera_id: str) -> dict:
    for cam in layout["cameras"]:
        if cam["camera_id"] == camera_id:
            return cam
    return {}


# ── Drawing ───────────────────────────────────────────────────────────────────

def _draw_entry_line(frame, line_dict: dict, direction: str) -> None:
    if not line_dict:
        return
    p1 = (line_dict["x1"], line_dict["y1"])
    p2 = (line_dict["x2"], line_dict["y2"])
    cv2.line(frame, p1, p2, _C_LINE, 3, cv2.LINE_AA)
    mx = (p1[0] + p2[0]) // 2 + 8
    my = (p1[1] + p2[1]) // 2
    cv2.putText(frame, direction, (mx, my),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, _C_LINE, 1, cv2.LINE_AA)


def _draw_hud(frame, inside: int, frame_num: int) -> None:
    h, w = frame.shape[:2]
    label = f"Inside: {inside}"
    (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.85, 2)
    cv2.rectangle(frame, (w - tw - 22, 6), (w - 4, th + 18), _C_BG, -1)
    cv2.putText(frame, label, (w - tw - 14, th + 12),
                cv2.FONT_HERSHEY_SIMPLEX, 0.85, _C_HUD, 2, cv2.LINE_AA)
    cv2.putText(frame, f"frame {frame_num}", (10, 28),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, _C_HUD, 1, cv2.LINE_AA)


def _draw_banner(frame, event: dict, frames_left: int) -> None:
    h, w = frame.shape[:2]
    etype  = event["event_type"]
    color  = _C_ENTRY if etype == "ENTRY" else _C_EXIT
    vid    = event.get("visitor_id", "?")
    meta   = event.get("metadata", {})
    dwell  = event.get("dwell_ms", 0)

    if etype == "ENTRY":
        gsize = meta.get("group_size", 1)
        gid   = meta.get("group_id")
        line1 = f"{etype}   {vid}"
        line2 = f"group size: {gsize}  id: {gid}" if gsize > 1 and gid else ""
    else:
        line1 = f"{etype}    {vid}"
        line2 = f"dwell: {_fmt_dwell(dwell)}" if dwell > 0 else ""

    bh = 72 if line2 else 46
    # Semi-transparent background
    roi    = frame[h - bh:h, 0:w]
    bg     = roi.copy()
    bg[:]  = _C_BG
    alpha  = min(1.0, frames_left / (BANNER_FRAMES * 0.3))
    cv2.addWeighted(bg, 0.72 * alpha, roi, 1 - 0.72 * alpha, 0, roi)

    cv2.putText(frame, line1, (18, h - bh + 30),
                cv2.FONT_HERSHEY_SIMPLEX, 1.0, color, 2, cv2.LINE_AA)
    if line2:
        cv2.putText(frame, line2, (18, h - bh + 60),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, _C_HUD, 1, cv2.LINE_AA)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--events",  required=True, help="L4 entry_events.jsonl")
    ap.add_argument("--video",   required=True, help="Entry-camera video (.mp4)")
    ap.add_argument("--layout",  required=True, help="store_layout.json")
    ap.add_argument("--tracks",  default=None,
                    help="L2 tracks JSONL for the same camera (enables accurate frame sync)")
    ap.add_argument("--speed",   type=float, default=1.0, help="Playback speed (default 1.0)")
    ap.add_argument("--out",     default=None, help="Save annotated video to this path")
    args = ap.parse_args()

    events_path = Path(args.events)
    video_path  = Path(args.video)
    layout_path = Path(args.layout)

    if not events_path.exists():
        print(f"[ERROR] Events file not found: {events_path}")
        sys.exit(1)
    if not video_path.exists():
        print(f"[ERROR] Video not found: {video_path}")
        sys.exit(1)

    with open(layout_path, encoding="utf-8") as f:
        layout = json.load(f)

    events = _load_events(events_path)
    if not events:
        print("[WARN] No events in file — will just show entry line on video.")

    # Build frame→timestamp map from L2 tracks if provided
    frame_ts_map: dict[int, datetime] = {}
    if args.tracks:
        tp = Path(args.tracks)
        if tp.exists():
            frame_ts_map = _build_frame_ts_map(tp)
            print(f"Loaded {len(frame_ts_map)} frame timestamps from tracks file.")
        else:
            print(f"[WARN] Tracks file not found: {tp}  (falling back to fps-based sync)")

    # Determine clip start for fps-based fallback
    clip_start: datetime | None = None
    if frame_ts_map:
        clip_start = min(frame_ts_map.values())
    elif events:
        clip_start = _parse_ts(events[0]["timestamp"])

    # Camera metadata from layout
    camera_id = events[0]["camera_id"] if events else ""
    cam_data  = _get_entry_cam(layout, camera_id)
    line_dict = cam_data.get("entry_line")
    direction = cam_data.get("entry_direction", "")

    # Open video
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        print(f"[ERROR] Cannot open video: {video_path}")
        sys.exit(1)

    fps    = cap.get(cv2.CAP_PROP_FPS) or 30.0
    vw     = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    vh     = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total  = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    # Map each event to a frame number
    def _event_frame(ev: dict) -> int:
        ev_ts = _parse_ts(ev["timestamp"])
        if frame_ts_map:
            return min(frame_ts_map.keys(),
                       key=lambda k: abs((frame_ts_map[k] - ev_ts).total_seconds()))
        if clip_start:
            delta = (ev_ts - clip_start).total_seconds()
            return max(0, round(delta * fps))
        return 0

    frame_events: dict[int, list[dict]] = {}
    for ev in events:
        fn = _event_frame(ev)
        frame_events.setdefault(fn, []).append(ev)

    # Set up writer or window
    writer = None
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        writer = cv2.VideoWriter(
            str(out_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (vw, vh)
        )
        print(f"Saving annotated video → {out_path}")
    else:
        cv2.namedWindow("L4 Entry Events", cv2.WINDOW_NORMAL)
        print(f"Window open — SPACE to pause, Q to quit")

    inside_count = 0
    active_banners: list[dict] = []  # {"event": dict, "frames_left": int}
    delay_ms = max(1, int(1000 / fps / args.speed))
    paused   = False
    frame_num = 0

    print(f"Video: {vw}×{vh} @ {fps:.0f}fps  |  {len(events)} events  |  {total} frames")

    while True:
        if not paused:
            ret, frame = cap.read()
            if not ret:
                break

            # Fire events that belong to this frame
            for ev in frame_events.get(frame_num, []):
                if ev["event_type"] == "ENTRY":
                    inside_count += 1
                elif ev["event_type"] == "EXIT":
                    inside_count = max(0, inside_count - 1)
                active_banners.append({"event": ev, "frames_left": BANNER_FRAMES})

        # Overlay entry line
        _draw_entry_line(frame, line_dict, direction)

        # Show banners (up to 2 at once — latest on top)
        for b in active_banners[-2:]:
            _draw_banner(frame, b["event"], b["frames_left"])

        # Decay banners
        for b in active_banners:
            b["frames_left"] -= 1
        active_banners = [b for b in active_banners if b["frames_left"] > 0]

        _draw_hud(frame, inside_count, frame_num)

        if writer:
            writer.write(frame)
        else:
            cv2.imshow("L4 Entry Events", frame)
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
        print(f"Done — saved to {args.out}")
    else:
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
