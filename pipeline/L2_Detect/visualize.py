#!/usr/bin/env python3
"""
L2 Tracker Visualizer
=====================
Overlays JSONL detections onto the source video.

What you see
------------
  Coloured box  — one consistent colour per track ID
  ID:4  0.87    — track ID and detection confidence above the box
  [OCC]         — appended to the label when occluded=True
  White dot     — centroid
  Top-left HUD  — frame number + ISO timestamp

Usage
-----
  # live window (default)
  python visualize.py --jsonl output/STORE_STORE_1/CAM_ZONE_01__CAM 2 - zone.jsonl ^
                      --video ../../L1_StoreLayout_selfannotate/input/Store 1/CAM 2 - zone.mp4

  # faster playback
  python visualize.py --jsonl ... --video ... --speed 2.0

  # save to file instead of displaying
  python visualize.py --jsonl ... --video ... --out review.mp4

Keyboard (live window only)
---------------------------
  Q / Esc  quit
  Space    pause / resume
"""

import json
import subprocess
import sys
from pathlib import Path

# ── auto-install opencv if missing ───────────────────────────────────────────
try:
    import cv2
except ImportError:
    print("opencv-python not found — installing…")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "opencv-python"])
    import cv2

import argparse
import numpy as np


# ── BGR colour palette — one per track ID (cycles) ───────────────────────────
_PALETTE = [
    (  0, 230,  76),   # green
    (  0, 165, 255),   # orange
    (  0,  60, 255),   # red
    (255,  60,   0),   # blue
    (255,   0, 200),   # magenta
    (  0, 220, 220),   # yellow
    (180,   0, 255),   # purple
    (255, 160,   0),   # sky
    (  0, 180, 120),   # teal
    (120,   0, 180),   # maroon
    ( 80, 200,   0),   # lime
    (  0, 100, 200),   # amber
]


def _colour(track_id: int) -> tuple:
    return _PALETTE[track_id % len(_PALETTE)]


def load_jsonl(path: str) -> dict:
    """Return {frame_number: record} dict for O(1) lookup during playback."""
    records: dict[int, dict] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            records[r["frame"]] = r
    return records


def _put_text_shadowed(img, text, pos, scale, colour, thickness=1):
    """Draw text with a dark shadow for legibility on any background."""
    x, y = pos
    cv2.putText(img, text, (x + 1, y + 1),
                cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thickness + 1, cv2.LINE_AA)
    cv2.putText(img, text, (x, y),
                cv2.FONT_HERSHEY_SIMPLEX, scale, colour, thickness, cv2.LINE_AA)


def draw_frame(img: np.ndarray, record: dict | None) -> np.ndarray:
    out = img.copy()

    if record is None:
        return out

    # ── top-left HUD ──────────────────────────────────────────────────────────
    hud = f"frame {record['frame']}   {record.get('timestamp', '')}"
    _put_text_shadowed(out, hud, (10, 30), 0.6, (255, 255, 255), 1)

    dropped = record.get("frame_meta", {}).get("dropped", False)
    if dropped:
        _put_text_shadowed(out, "DROPPED", (10, 58), 0.6, (0, 0, 255), 2)
        return out

    for t in record.get("tracks", []):
        tid    = t["track_id"]
        x1, y1, x2, y2 = t["bbox"]
        cx, cy = t["centroid"]
        conf   = t["confidence"]
        occ    = t.get("occluded", False)
        colour = _colour(tid)

        # bbox
        thickness = 3 if occ else 2
        cv2.rectangle(out, (x1, y1), (x2, y2), colour, thickness)

        # label
        label = f"ID:{tid}  {conf:.2f}"
        if occ:
            label += "  [OCC]"

        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
        lx = max(0, x1)
        ly = max(th + 6, y1)
        cv2.rectangle(out, (lx, ly - th - 6), (lx + tw + 6, ly), colour, cv2.FILLED)
        cv2.putText(out, label, (lx + 3, ly - 3),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (0, 0, 0), 1, cv2.LINE_AA)

        # centroid dot
        cv2.circle(out, (cx, cy), 5, (255, 255, 255), cv2.FILLED)
        cv2.circle(out, (cx, cy), 5, (0, 0, 0), 1)

    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="L2 Tracker Visualizer")
    ap.add_argument("--jsonl",  required=True, help="Path to .jsonl output file")
    ap.add_argument("--video",  required=True, help="Path to source .mp4 / .mov video")
    ap.add_argument("--speed",  type=float, default=1.0,
                    help="Playback speed multiplier (default 1.0, try 2.0 to skim)")
    ap.add_argument("--out",    default=None,
                    help="Write annotated video to this path instead of showing live window")
    args = ap.parse_args()

    if not Path(args.jsonl).exists():
        sys.exit(f"JSONL not found: {args.jsonl}")
    if not Path(args.video).exists():
        sys.exit(f"Video not found: {args.video}")

    print(f"Loading JSONL… ", end="", flush=True)
    records = load_jsonl(args.jsonl)
    print(f"{len(records)} records ({min(records)}-{max(records)} frames)")

    cap = cv2.VideoCapture(args.video)
    if not cap.isOpened():
        sys.exit(f"Cannot open video: {args.video}")

    fps   = cap.get(cv2.CAP_PROP_FPS) or 30.0
    vid_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    vid_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"Video: {vid_w}×{vid_h}  {fps:.1f} fps  ~{total} frames")

    # ── output writer or live window ──────────────────────────────────────────
    writer = None
    if args.out:
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(args.out, fourcc, fps, (vid_w, vid_h))
        if not writer.isOpened():
            sys.exit(f"Cannot create output file: {args.out}")
        print(f"Writing annotated video → {args.out}")
    else:
        disp_w = min(vid_w, 1280)
        disp_h = int(vid_h * disp_w / vid_w)
        cv2.namedWindow("L2 Tracker", cv2.WINDOW_NORMAL)
        cv2.resizeWindow("L2 Tracker", disp_w, disp_h)
        print("Press  Q / Esc  to quit   |   Space  to pause/resume")

    delay = max(1, int(1000 / (fps * args.speed)))

    frame_num = 0
    paused    = False

    while True:
        if not paused:
            ret, frame = cap.read()
            if not ret:
                break

            record = records.get(frame_num)
            vis    = draw_frame(frame, record)

            if writer:
                writer.write(vis)
                if frame_num % 300 == 0:
                    print(f"  …frame {frame_num}/{total}", flush=True)
            else:
                cv2.imshow("L2 Tracker", vis)

            frame_num += 1

        if not writer:
            key = cv2.waitKey(1 if paused else delay) & 0xFF
            if key in (ord("q"), 27):   # Q or Esc
                break
            elif key == ord(" "):
                paused = not paused
                print("Paused." if paused else "Resumed.")

    cap.release()
    if writer:
        writer.release()
        print(f"Saved → {args.out}")
    else:
        cv2.destroyAllWindows()
    print("Done.")


if __name__ == "__main__":
    main()
