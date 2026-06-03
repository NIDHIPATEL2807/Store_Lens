#!/usr/bin/env python3
"""
L1 Store Layout — Manual Annotation Pipeline
=============================================
Extracts one frame per camera, then opens the interactive
annotation window so you can draw zone polygons with your mouse.

Usage:
    python run_annotation.py                                    # all stores in input/
    python run_annotation.py --store "Store 1"                  # one store only
    python run_annotation.py --store "Store 1" --camera CAM_ENTRY_01   # one camera only
    python run_annotation.py --open 09:00 --close 21:00
"""

import argparse
import json
import sys
from pathlib import Path

BASE_DIR   = Path(__file__).parent
INPUT_DIR  = BASE_DIR / "input"
OUTPUT_DIR = BASE_DIR / "output"

from extract_frames  import extract_frames
from annotate_zones  import annotate_camera, annotate_entry_line


def store_name_to_id(name: str) -> str:
    return "STORE_" + name.upper().replace(" ", "_")


def _skeleton(store_id: str, cameras: list[dict],
              open_time: str, close_time: str) -> dict:
    """Blank layout — cameras wired in, no zones yet."""
    return {
        "store_id":   store_id,
        "open_hours": {"open": open_time, "close": close_time},
        "cameras": [
            {
                "camera_id":    cam["camera_id"],
                "camera_type":  cam["camera_type"],
                "frame_width":  cam["frame_width"],
                "frame_height": cam["frame_height"],
                "zones":        [],
            }
            for cam in cameras
        ],
        "boh_zones":          [],
        "billing_camera_id":  next((c["camera_id"] for c in cameras
                                    if c["camera_type"] == "billing"), None),
        "entry_camera_id":    next((c["camera_id"] for c in cameras
                                    if c["camera_type"] == "entry"), None),
    }


def _refresh_derived(layout: dict) -> None:
    """Re-derive billing_camera_id, entry_camera_id, boh_zones from zones list."""
    layout["billing_camera_id"] = next(
        (c["camera_id"] for c in layout["cameras"] if c.get("camera_type") == "billing"), None)
    layout["entry_camera_id"] = next(
        (c["camera_id"] for c in layout["cameras"] if c.get("camera_type") == "entry"), None)
    layout["boh_zones"] = list({
        z["zone_id"]
        for c in layout["cameras"]
        for z in c.get("zones", [])
        if z.get("zone_type") == "boh"
    })


def process_store(store_dir: Path, open_time: str, close_time: str,
                  camera_filter: str | None = None) -> None:
    store_id    = store_name_to_id(store_dir.name)
    store_out   = OUTPUT_DIR / store_dir.name
    frames_dir  = store_out / "frames"
    layout_file = store_out / "store_layout.json"
    store_out.mkdir(parents=True, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"  {store_dir.name}  →  {store_id}")
    print(f"{'='*60}")

    # ── Step 1: Extract frames ────────────────────────────────────────
    print("\n[1/2] Extracting camera frames...")
    cameras = extract_frames(str(store_dir), str(frames_dir))
    if not cameras:
        print("  [ERROR] No camera videos found — skipping store.")
        return

    # ── Step 2: Load or create skeleton layout ────────────────────────
    if layout_file.exists():
        print(f"\n[2/2] Existing layout found — re-annotating zones.")
        with open(layout_file, encoding="utf-8") as f:
            layout = json.load(f)
    else:
        print(f"\n[2/2] Creating fresh layout skeleton...")
        layout = _skeleton(store_id, cameras, open_time, close_time)

    # ── Step 3: Annotate each camera ─────────────────────────────────
    for cam_data in layout["cameras"]:
        if camera_filter and cam_data["camera_id"] != camera_filter:
            continue
        cam_id     = cam_data["camera_id"]
        frame_path = frames_dir / f"{cam_id}.jpg"

        if not frame_path.exists():
            print(f"\n  [SKIP] Frame not found: {frame_path.name}")
            continue

        print(f"\n  ── {cam_id}  ({cam_data['camera_type']}) ──")
        existing_zones = [z["zone_name"] for z in cam_data.get("zones", [])]
        if existing_zones:
            print(f"     Existing zones: {existing_zones}")
        else:
            print("     No zones yet — press A in the window to add them.")

        updated = annotate_camera(str(frame_path), cam_data)

        if updated.get("camera_type") == "entry":
            updated = annotate_entry_line(str(frame_path), updated)

        # write back
        for i, c in enumerate(layout["cameras"]):
            if c["camera_id"] == cam_id:
                layout["cameras"][i] = updated
                break

        # auto-save after every camera
        _refresh_derived(layout)
        with open(layout_file, "w", encoding="utf-8") as f:
            json.dump(layout, f, indent=2)
        print(f"  [auto-save] {layout_file.relative_to(BASE_DIR)}")

    # ── Final save ────────────────────────────────────────────────────
    _refresh_derived(layout)
    with open(layout_file, "w", encoding="utf-8") as f:
        json.dump(layout, f, indent=2)

    total_zones = sum(len(c.get("zones", [])) for c in layout["cameras"])
    print(f"\n  ✅  Done  — {total_zones} zones across {len(layout['cameras'])} cameras")
    print(f"      Saved → {layout_file}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store",  default=None, help="Process only this store folder name")
    ap.add_argument("--camera", default=None, help="Re-annotate only this camera ID (e.g. CAM_ENTRY_01)")
    ap.add_argument("--open",   default="10:00", dest="open_time")
    ap.add_argument("--close",  default="22:00", dest="close_time")
    args = ap.parse_args()

    if args.camera and not args.store:
        print("[ERROR] --camera requires --store (we need to know which store)")
        sys.exit(1)

    if args.store:
        store_dirs = [INPUT_DIR / args.store]
        if not store_dirs[0].is_dir():
            print(f"[ERROR] Not found: {store_dirs[0]}")
            sys.exit(1)
    else:
        store_dirs = sorted(d for d in INPUT_DIR.iterdir() if d.is_dir())

    if not store_dirs:
        print(f"[ERROR] No store directories in {INPUT_DIR}")
        sys.exit(1)

    for store_dir in store_dirs:
        try:
            process_store(store_dir, args.open_time, args.close_time, args.camera)
        except Exception as exc:
            import traceback
            print(f"\n[ERROR] {store_dir.name}: {exc}")
            traceback.print_exc()


if __name__ == "__main__":
    main()
