"""
Layer 2 — Multi-Object Tracking (ByteTrack)
Model:  ByteTrack via supervision
Input:  ../input/bounding_boxes/<stem>_detections.json   (Layer 1 output, same folder name)
Output: ../output/tracked_bboxes/<stem>_tracks.json

Output schema per item:
  {
    "track_id":   7,
    "bbox":       [x1, y1, x2, y2],
    "confidence": 0.87,
    "frame_id":   450,
    "velocity":   [dx, dy]     # pixel displacement of centre vs previous frame
  }
"""

from __future__ import annotations

from pathlib import Path
from collections import defaultdict
import json
import numpy as np
import supervision as sv

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT       = Path(__file__).parent.parent
INPUT_DIR  = ROOT / "input"  / "bounding_boxes"
OUTPUT_DIR = ROOT / "output" / "tracked_bboxes"

# ── Config ────────────────────────────────────────────────────────────────────
FPS              = 15.0
TRACK_THRESHOLD  = 0.25
LOST_BUFFER_SEC  = 2          # seconds before a lost track is dropped
MATCH_THRESHOLD  = 0.8


def track_video(detections_path: Path) -> list[dict]:
    with open(detections_path) as f:
        raw: list[dict] = json.load(f)

    # group by frame
    by_frame: dict[int, list[dict]] = defaultdict(list)
    for d in raw:
        by_frame[d["frame_id"]].append(d)

    tracker = sv.ByteTrack(
        track_activation_threshold=TRACK_THRESHOLD,
        lost_track_buffer=int(FPS * LOST_BUFFER_SEC),
        minimum_matching_threshold=MATCH_THRESHOLD,
        frame_rate=int(FPS),
    )

    prev_centers: dict[int, tuple[float, float]] = {}
    results: list[dict] = []

    for frame_id in sorted(by_frame):
        frame_dets = by_frame[frame_id]

        if frame_dets:
            xyxy  = np.array([d["bbox"] for d in frame_dets], dtype=float)
            conf  = np.array([d["confidence"] for d in frame_dets], dtype=float)
            cls   = np.zeros(len(frame_dets), dtype=int)
            sv_dets = sv.Detections(xyxy=xyxy, confidence=conf, class_id=cls)
        else:
            sv_dets = sv.Detections.empty()

        tracked = tracker.update_with_detections(sv_dets)

        if tracked.tracker_id is None:
            continue

        for i in range(len(tracked)):
            tid = tracked.tracker_id[i]
            if tid is None:
                continue

            x1, y1, x2, y2 = map(float, tracked.xyxy[i])
            conf_val = float(tracked.confidence[i]) if tracked.confidence is not None else 0.0
            cx, cy   = (x1 + x2) / 2, (y1 + y2) / 2

            if tid in prev_centers:
                dx = cx - prev_centers[tid][0]
                dy = cy - prev_centers[tid][1]
            else:
                dx, dy = 0.0, 0.0

            prev_centers[tid] = (cx, cy)

            results.append({
                "track_id":   int(tid),
                "bbox":       [x1, y1, x2, y2],
                "confidence": conf_val,
                "frame_id":   frame_id,
                "velocity":   [round(dx, 3), round(dy, 3)],
            })

    return results


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    det_files = sorted(INPUT_DIR.glob("*_detections.json"))
    if not det_files:
        print(f"[Layer 2] No detection JSONs found in {INPUT_DIR}")
        print(f"[Layer 2] Copy Layer 1 output (output/bounding_boxes/) into input/bounding_boxes/")
        return

    for det_path in det_files:
        stem = det_path.stem.replace("_detections", "")
        print(f"[Layer 2] Tracking {det_path.name} …")
        tracks = track_video(det_path)

        out = OUTPUT_DIR / f"{stem}_tracks.json"
        out.write_text(json.dumps(tracks, indent=2))
        print(f"[Layer 2] → {len(tracks)} track entries  →  {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
