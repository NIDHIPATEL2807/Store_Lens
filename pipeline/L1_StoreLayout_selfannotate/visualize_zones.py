"""
Overlay zone polygons from store_layout.json onto camera frames.

Usage:
    python visualize_zones.py                  # all stores
    python visualize_zones.py --store "Store 1"
    python visualize_zones.py --show            # display with cv2.imshow too

Output: output/<StoreName>/viz/<CAM_ID>_zones.jpg
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"

# BGR colors per zone type
ZONE_COLORS = {
    "shelf":   (50,  200, 50),
    "billing": (200, 120, 30),
    "entry":   (30,  200, 220),
    "boh":     (50,  50,  210),
    "floor":   (160, 160, 160),
}
DEFAULT_COLOR = (180, 180, 180)
FILL_ALPHA    = 0.25   # polygon fill transparency
FONT          = cv2.FONT_HERSHEY_SIMPLEX


def draw_zones(frame: np.ndarray, camera: dict) -> np.ndarray:
    overlay = frame.copy()

    for zone in camera.get("zones", []):
        pts = np.array(zone["polygon"], dtype=np.int32)
        color = ZONE_COLORS.get(zone["zone_type"], DEFAULT_COLOR)

        # Semi-transparent fill
        cv2.fillPoly(overlay, [pts], color)

        # Solid border
        cv2.polylines(frame, [pts], isClosed=True, color=color, thickness=3)

        # Label at centroid
        cx = int(pts[:, 0].mean())
        cy = int(pts[:, 1].mean())
        label = zone["zone_name"]
        _draw_label(frame, label, cx, cy, color)

    # Blend fill layer
    cv2.addWeighted(overlay, FILL_ALPHA, frame, 1 - FILL_ALPHA, 0, frame)

    # Entry line (if present)
    el = camera.get("entry_line")
    if el:
        cv2.line(
            frame,
            (el["x1"], el["y1"]), (el["x2"], el["y2"]),
            (0, 255, 255), thickness=3, lineType=cv2.LINE_AA,
        )
        direction = camera.get("entry_direction", "")
        _draw_label(frame, f"entry: {direction}", el["x1"] + 8, el["y1"] + 30, (0, 255, 255))

    # Camera ID watermark
    cv2.putText(
        frame, camera["camera_id"],
        (14, frame.shape[0] - 14), FONT, 0.7, (255, 255, 255), 2, cv2.LINE_AA,
    )

    return frame


def _draw_label(img: np.ndarray, text: str, cx: int, cy: int, color: tuple) -> None:
    scale, thickness = 0.55, 1
    (tw, th), baseline = cv2.getTextSize(text, FONT, scale, thickness)
    x = max(4, min(cx - tw // 2, img.shape[1] - tw - 4))
    y = max(th + 4, min(cy + th // 2, img.shape[0] - baseline - 4))

    # Dark background pill for readability
    cv2.rectangle(img, (x - 3, y - th - 3), (x + tw + 3, y + baseline + 1), (20, 20, 20), -1)
    cv2.putText(img, text, (x, y), FONT, scale, color, thickness, cv2.LINE_AA)


def process_store(store_out: Path, show: bool) -> None:
    layout_file = store_out / "store_layout.json"
    if not layout_file.exists():
        print(f"  [SKIP] No store_layout.json in {store_out}")
        return

    with open(layout_file, encoding="utf-8") as f:
        layout = json.load(f)

    frames_dir = store_out / "frames"
    viz_dir    = store_out / "viz"
    viz_dir.mkdir(exist_ok=True)

    print(f"\n{layout['store_id']}  ({len(layout['cameras'])} cameras)")

    for cam in layout["cameras"]:
        cam_id     = cam["camera_id"]
        frame_path = frames_dir / f"{cam_id}.jpg"

        if not frame_path.exists():
            print(f"  [SKIP] Frame not found: {frame_path.name}")
            continue

        frame = cv2.imread(str(frame_path))
        if frame is None:
            print(f"  [WARN] Could not read {frame_path.name}")
            continue

        annotated = draw_zones(frame, cam)

        out_path = viz_dir / f"{cam_id}_zones.jpg"
        cv2.imwrite(str(out_path), annotated)
        print(f"  [OK]  {cam_id:24s} -> {out_path.relative_to(BASE_DIR)}")

        if show:
            cv2.imshow(f"{layout['store_id']} — {cam_id}", annotated)
            cv2.waitKey(0)

    if show:
        cv2.destroyAllWindows()


def main() -> None:
    parser = argparse.ArgumentParser(description="Visualize zone polygons on camera frames")
    parser.add_argument("--store", help="Process only this store folder name")
    parser.add_argument("--show", action="store_true", help="Display images interactively")
    args = parser.parse_args()

    if args.store:
        store_dirs = [OUTPUT_DIR / args.store]
        if not store_dirs[0].is_dir():
            print(f"[ERROR] Not found: {store_dirs[0]}")
            sys.exit(1)
    else:
        store_dirs = sorted(d for d in OUTPUT_DIR.iterdir() if d.is_dir())

    if not store_dirs:
        print(f"[ERROR] No output directories found under {OUTPUT_DIR}")
        print("        Run run_pipeline.py first.")
        sys.exit(1)

    for store_dir in store_dirs:
        process_store(store_dir, args.show)

    print("\n[DONE] Visualizations saved to output/<StoreName>/viz/")


if __name__ == "__main__":
    main()
