#!/usr/bin/env python3
"""
L2 Detect & Track — Entry Point
=================================
Reads a store_layout.json from L1, matches video files to camera configs,
and runs per-frame person detection + ByteTrack.

Output: one .jsonl file per camera per clip in output/<store_id>/

Usage (Windows cmd):
    # Single video
    python run_l2.py --layout "../L1_StoreLayout_selfannotate/output/Store 1/store_layout.json" ^
                     --video  "../L1_StoreLayout_selfannotate/input/Store 1/CAM 1 - zone.mp4" ^
                     --clip_start 2026-03-08T10:00:00Z

    # All videos in a folder
    python run_l2.py --layout "../L1_StoreLayout_selfannotate/output/Store 1/store_layout.json" ^
                     --video_dir "../L1_StoreLayout_selfannotate/input/Store 1/"

    # Override frame skip and model
    python run_l2.py --layout ... --video_dir ... --frame_skip 5 --model yolov8s.pt
"""

import argparse
import json
import logging
import sys
from pathlib import Path

BASE_DIR   = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"

from detect_track import DetectTracker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("L2")

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".hevc"}


def match_camera(layout: dict, video_path: str) -> dict | None:
    """
    Match a video file to a camera config entry.
    Strategy: exact camera_id in filename → camera_type keyword → None.
    """
    stem = Path(video_path).stem.lower()

    # 1 — exact camera_id match (e.g. "CAM_ZONE_01" in filename)
    for cam in layout["cameras"]:
        if cam["camera_id"].lower().replace("_", " ") in stem or \
           cam["camera_id"].lower() in stem.replace(" ", "_"):
            return cam

    # 2 — camera_type keyword (billing / entry / zone)
    for cam in layout["cameras"]:
        if cam.get("camera_type", "") in stem:
            return cam

    return None


def run_camera(
    layout: dict,
    cam_cfg: dict,
    video_path: str,
    clip_start: str | None,
    model_path: str,
    frame_skip: int,
) -> None:
    store_id = layout["store_id"]
    cam_id   = cam_cfg["camera_id"]
    out_dir  = OUTPUT_DIR / store_id
    out_file = out_dir / f"{cam_id}__{Path(video_path).stem}.jsonl"

    logger.info("%-20s  ←  %s", cam_id, Path(video_path).name)

    tracker = DetectTracker(
        camera_config=cam_cfg,
        store_id=store_id,
        model_path=model_path,
        frame_skip=frame_skip,
    )
    summary = tracker.process_video(
        video_path=video_path,
        output_path=str(out_file),
        clip_start_utc=clip_start,
    )

    logger.info(
        "%-20s  done — %d frames | %d low-conf dropped  →  %s",
        cam_id,
        summary["frames_processed"],
        summary["low_conf_dropped"],
        Path(summary["output"]).name,
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="L2 Person Detection & Tracking")
    ap.add_argument("--layout",      required=True,
                    help="store_layout.json path (from L1 output)")
    ap.add_argument("--video",       default=None,
                    help="Single video file")
    ap.add_argument("--video_dir",   default=None,
                    help="Directory of video files — all will be processed")
    ap.add_argument("--camera_id",   default=None,
                    help="Force-assign all videos to this camera_id")
    ap.add_argument("--clip_start",  default=None,
                    help="Clip start UTC e.g. 2026-03-08T10:00:00Z  "
                         "(parsed from filename if omitted)")
    ap.add_argument("--model",       default="yolov8n.pt",
                    help="YOLOv8 weights file (default: yolov8n.pt)")
    ap.add_argument("--frame_skip",  default=3, type=int,
                    help="Process every Nth frame — 3 = 5 fps at 15 fps source (default 3)")
    args = ap.parse_args()

    if not args.video and not args.video_dir:
        ap.error("Provide --video or --video_dir")

    with open(args.layout, encoding="utf-8") as f:
        layout = json.load(f)

    # collect video files
    if args.video:
        videos = [Path(args.video)]
    else:
        videos = sorted(
            p for p in Path(args.video_dir).iterdir()
            if p.suffix.lower() in VIDEO_EXTS
        )

    if not videos:
        logger.error("No video files found.")
        sys.exit(1)

    logger.info("Store : %s", layout["store_id"])
    logger.info("Videos: %d  |  frame_skip=%d  |  model=%s",
                len(videos), args.frame_skip, args.model)

    failed = []
    for vp in videos:
        if args.camera_id:
            matches = [c for c in layout["cameras"] if c["camera_id"] == args.camera_id]
            cam_cfg = matches[0] if matches else None
        else:
            cam_cfg = match_camera(layout, str(vp))

        if cam_cfg is None:
            logger.warning("No camera config matched for %s — skipping.", vp.name)
            continue

        try:
            run_camera(layout, cam_cfg, str(vp), args.clip_start, args.model, args.frame_skip)
        except Exception as exc:
            import traceback
            logger.error("FAILED %s: %s", vp.name, exc)
            traceback.print_exc()
            failed.append(vp.name)

    if failed:
        logger.error("Failed clips: %s", failed)
        sys.exit(1)
    else:
        logger.info("All done. Output in %s", OUTPUT_DIR)


if __name__ == "__main__":
    main()
