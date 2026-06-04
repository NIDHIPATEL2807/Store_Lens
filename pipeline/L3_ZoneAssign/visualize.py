#!/usr/bin/env python3
"""
L3 Zone Assignment Visualizer
==============================
Overlays zone polygons on the source video and flashes them when a visitor
enters or exits. Proves zone detection is working.

What you see:
  • All annotated zones drawn as filled semi-transparent polygons
  • Zone label centred inside each polygon
  • Zone FLASHES bright green on ZONE_ENTER, red on ZONE_EXIT
  • Bottom banner shows last event (zone + event type)
  • Top-left HUD: frame number + visitor count per zone

Usage:
    python visualize.py ^
        --zone_events  output/store1/CAM_ZONE_01__CAM 1 - zone_zone_events.jsonl ^
        --tracks       ../L2_Detect/output/STORE_STORE_1/CAM_ZONE_01__CAM 1 - zone.jsonl ^
        --layout       ../L1_StoreLayout_selfannotate/output/Store 1/store_layout.json ^
        --video        ../L1_StoreLayout_selfannotate/input/Store 1/CAM 1 - zone.mp4 ^
        --out          ../output/viz/store1_zone.mp4
"""

try:
    import cv2
except ImportError:
    import subprocess, sys as _sys
    subprocess.check_call([_sys.executable, "-m", "pip", "install", "opencv-python"])
    import cv2

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

# ── Colour palette for zones ──────────────────────────────────────────────────
_ZONE_COLOURS = [
    (255, 100,  50),
    ( 50, 200, 255),
    (100, 255, 100),
    (200,  50, 255),
    (255, 200,  50),
    ( 50, 255, 200),
    (255,  50, 150),
    (150, 255,  50),
    ( 50, 150, 255),
    (255, 150,  50),
]
_FLASH_ENTER = (50, 255, 50)
_FLASH_EXIT  = (50, 50, 255)
FLASH_FRAMES = 20


def _parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _poly_centroid(pts: list) -> tuple[int, int]:
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return int(sum(xs) / len(xs)), int(sum(ys) / len(ys))


def _load_zone_events(path: Path) -> list[dict]:
    events = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    events.sort(key=lambda e: e["timestamp"])
    return events


def _build_frame_ts_map(tracks_path: Path) -> dict[int, datetime]:
    fmap = {}
    with open(tracks_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if "frame" in rec and "timestamp" in rec:
                fmap[rec["frame"]] = _parse_ts(rec["timestamp"])
    return fmap


def _event_to_frame(ev: dict, frame_ts_map: dict, clip_start: datetime, fps: float) -> int:
    ev_ts = _parse_ts(ev["timestamp"])
    if frame_ts_map:
        return min(frame_ts_map.keys(),
                   key=lambda k: abs((frame_ts_map[k] - ev_ts).total_seconds()))
    delta = (ev_ts - clip_start).total_seconds()
    return max(0, round(delta * fps))


def _build_zone_map(layout: dict, camera_id: str) -> dict[str, dict]:
    """Returns {zone_id: {pts, colour, label}} for all zones on this camera."""
    zone_map = {}
    colour_idx = 0
    for cam in layout.get("cameras", []):
        if cam["camera_id"] != camera_id:
            continue
        for zone in cam.get("zones", []):
            zid = zone.get("zone_id", "?")
            pts_raw = zone.get("polygon", zone.get("points", []))
            if not pts_raw:
                continue
            pts = [(int(p[0]), int(p[1])) for p in pts_raw]
            zone_map[zid] = {
                "pts":    pts,
                "colour": _ZONE_COLOURS[colour_idx % len(_ZONE_COLOURS)],
                "label":  zone.get("zone_name", zid),
            }
            colour_idx += 1
    return zone_map


def _draw_zones(frame: np.ndarray, zone_map: dict, flash_state: dict) -> None:
    overlay = frame.copy()
    for zid, z in zone_map.items():
        pts = np.array(z["pts"], dtype=np.int32)
        fl  = flash_state.get(zid)

        if fl and fl["frames_left"] > 0:
            colour = _FLASH_ENTER if fl["etype"] == "ZONE_ENTER" else _FLASH_EXIT
            alpha  = 0.6
        else:
            colour = z["colour"]
            alpha  = 0.25

        cv2.fillPoly(overlay, [pts], colour)
        cv2.polylines(frame, [pts], True, colour, 2, cv2.LINE_AA)
        cx, cy = _poly_centroid(z["pts"])
        label  = z["label"]
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1)
        cv2.putText(frame, label, (cx - tw // 2, cy + th // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 255), 1, cv2.LINE_AA)

    cv2.addWeighted(overlay, alpha, frame, 1 - alpha, 0, frame)


def _draw_hud(frame: np.ndarray, frame_num: int, zone_counts: dict) -> None:
    y = 22
    cv2.putText(frame, f"frame {frame_num}", (10, y),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (220, 220, 220), 1, cv2.LINE_AA)
    y += 20
    for zid, cnt in list(zone_counts.items())[:6]:
        cv2.putText(frame, f"{zid}: {cnt}", (10, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.42, (200, 200, 200), 1, cv2.LINE_AA)
        y += 16


def _draw_banner(frame: np.ndarray, text: str, etype: str) -> None:
    h, w = frame.shape[:2]
    colour = _FLASH_ENTER if etype == "ZONE_ENTER" else _FLASH_EXIT
    bg = frame[h - 36:h, 0:w].copy()
    bg[:] = (20, 20, 20)
    cv2.addWeighted(bg, 0.7, frame[h - 36:h, 0:w], 0.3, 0, frame[h - 36:h, 0:w])
    cv2.putText(frame, text, (12, h - 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, colour, 2, cv2.LINE_AA)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zone_events", required=True, help="L3 zone_events.jsonl")
    ap.add_argument("--tracks",      required=True, help="L2 tracks JSONL (same camera)")
    ap.add_argument("--layout",      required=True, help="store_layout.json")
    ap.add_argument("--video",       required=True, help="Source video (.mp4)")
    ap.add_argument("--out",         default=None,  help="Save annotated video here")
    ap.add_argument("--speed",       type=float, default=1.0)
    args = ap.parse_args()

    ze_path  = Path(args.zone_events)
    vid_path = Path(args.video)

    if not ze_path.exists():
        sys.exit(f"[ERROR] Zone events not found: {ze_path}")
    if not vid_path.exists():
        sys.exit(f"[ERROR] Video not found: {vid_path}")

    with open(args.layout, encoding="utf-8") as f:
        layout = json.load(f)

    events = _load_zone_events(ze_path)
    if not events:
        print("[WARN] No zone events — drawing static zones only.")

    camera_id = events[0]["camera_id"] if events else ""
    zone_map  = _build_zone_map(layout, camera_id)
    print(f"Camera: {camera_id}  |  Zones: {list(zone_map.keys())}  |  Events: {len(events)}")

    frame_ts_map: dict[int, datetime] = {}
    if Path(args.tracks).exists():
        frame_ts_map = _build_frame_ts_map(Path(args.tracks))
        print(f"Loaded {len(frame_ts_map)} frame timestamps")

    clip_start = min(frame_ts_map.values()) if frame_ts_map else (
        _parse_ts(events[0]["timestamp"]) if events else datetime.utcnow()
    )

    cap = cv2.VideoCapture(str(vid_path))
    if not cap.isOpened():
        sys.exit(f"[ERROR] Cannot open video: {vid_path}")

    fps   = cap.get(cv2.CAP_PROP_FPS) or 30.0
    vw    = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    vh    = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    print(f"Video: {vw}×{vh} @ {fps:.0f}fps  {total} frames")

    # Map events to frames
    frame_events: dict[int, list[dict]] = {}
    for ev in events:
        fn = _event_to_frame(ev, frame_ts_map, clip_start, fps)
        frame_events.setdefault(fn, []).append(ev)

    writer = None
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        writer = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (vw, vh))
        print(f"Saving → {out_path}")
    else:
        cv2.namedWindow("L3 Zones", cv2.WINDOW_NORMAL)
        print("SPACE to pause, Q to quit")

    flash_state:  dict[str, dict] = {}
    zone_counts:  dict[str, int]  = {zid: 0 for zid in zone_map}
    last_banner   = ("", "")
    frame_num     = 0
    paused        = False
    delay_ms      = max(1, int(1000 / fps / args.speed))

    while True:
        if not paused:
            ret, frame = cap.read()
            if not ret:
                break

            for ev in frame_events.get(frame_num, []):
                zid   = ev.get("zone_id", "")
                etype = ev.get("event_type", "")
                flash_state[zid] = {"frames_left": FLASH_FRAMES, "etype": etype}
                if etype == "ZONE_ENTER":
                    zone_counts[zid] = zone_counts.get(zid, 0) + 1
                last_banner = (f"{etype}  →  {zid}", etype)

            _draw_zones(frame, zone_map, flash_state)
            _draw_hud(frame, frame_num, zone_counts)
            if last_banner[0]:
                _draw_banner(frame, last_banner[0], last_banner[1])

            # Decay flashes
            for zid in list(flash_state.keys()):
                flash_state[zid]["frames_left"] -= 1
                if flash_state[zid]["frames_left"] <= 0:
                    del flash_state[zid]

            if writer:
                writer.write(frame)
                if frame_num % 300 == 0:
                    print(f"  …{frame_num}/{total}", flush=True)
            else:
                cv2.imshow("L3 Zones", frame)

            frame_num += 1

        if not writer:
            key = cv2.waitKey(1 if paused else delay_ms) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord(" "):
                paused = not paused

    cap.release()
    if writer:
        writer.release()
        print(f"Saved → {args.out}")
    else:
        cv2.destroyAllWindows()
    print("Done.")


if __name__ == "__main__":
    main()
