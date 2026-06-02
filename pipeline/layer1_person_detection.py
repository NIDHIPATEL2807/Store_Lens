"""
Layer 1 — Person Detection
Model:  YOLOv8n (ultralytics)
Input:  ../input/raw_video_frame/*.mp4  |  *.avi  |  *.mov
Output: ../output/bounding_boxes/<stem>_detections.json

Output schema per item:
  {
    "bbox":       [x1, y1, x2, y2],   # pixel coords, float
    "confidence": 0.87,
    "class":      "person",
    "frame_id":   450
  }
"""

from __future__ import annotations

from pathlib import Path
import json
import cv2
from ultralytics import YOLO

# ── Paths (relative to project root) ─────────────────────────────────────────
ROOT       = Path(__file__).parent.parent
INPUT_DIR  = ROOT / "input" / "raw_video_frame"
OUTPUT_DIR = ROOT / "output" / "bounding_boxes"
WEIGHTS    = Path(__file__).parent / "yolov8n.pt"

# ── Config ────────────────────────────────────────────────────────────────────
CONF_THRESHOLD = 0.5
DEVICE         = "cpu"
VIDEO_EXTS     = {".mp4", ".avi", ".mov", ".mkv"}


def detect_persons(video_path: Path, conf: float = CONF_THRESHOLD) -> list[dict]:
    model = YOLO(str(WEIGHTS))
    cap   = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    detections: list[dict] = []
    frame_id = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        results = model.predict(
            frame,
            conf=conf,
            classes=[0],        # class 0 = person in COCO
            device=DEVICE,
            verbose=False,
        )
        for r in results:
            for box in r.boxes:
                x1, y1, x2, y2 = map(float, box.xyxy[0].tolist())
                detections.append({
                    "bbox":       [x1, y1, x2, y2],
                    "confidence": float(box.conf[0]),
                    "class":      "person",
                    "frame_id":   frame_id,
                })

        frame_id += 1

    cap.release()
    return detections


def main(conf: float = CONF_THRESHOLD) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    videos = [p for p in INPUT_DIR.iterdir() if p.suffix.lower() in VIDEO_EXTS]
    if not videos:
        print(f"[Layer 1] No videos found in {INPUT_DIR}")
        return

    for video_path in sorted(videos):
        print(f"[Layer 1] Detecting persons in {video_path.name} …")
        dets = detect_persons(video_path, conf)

        out = OUTPUT_DIR / f"{video_path.stem}_detections.json"
        out.write_text(json.dumps(dets, indent=2))
        print(f"[Layer 1] → {len(dets)} detections  →  {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
