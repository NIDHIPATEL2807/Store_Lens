#!/usr/bin/env python3
"""
L1 Store Layout Pipeline
========================
Reads every store folder under input/, runs the full layout-extraction
pipeline, and writes output/<StoreName>/store_layout.json.

Usage:
    set OPENROUTER_API_KEY=<your-key>
    python run_pipeline.py

Optional overrides:
    python run_pipeline.py --store "Store 1"          # process one store only
    python run_pipeline.py --model gemini-1.5-flash   # choose Gemini model
    python run_pipeline.py --open 09:00 --close 21:00 # custom hours
"""
import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path

BASE_DIR = Path(__file__).parent
INPUT_DIR = BASE_DIR / "input"
OUTPUT_DIR = BASE_DIR / "output"

sys.path.insert(0, str(BASE_DIR))


def _load_dotenv() -> None:
    """Load .env from pipeline/ (parent of this script's directory)."""
    env_file = BASE_DIR.parent / ".env"
    if not env_file.exists():
        return
    with open(env_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

from extract_frames import extract_frames
from parse_layout import parse_layout
from calibrate_camera import calibrate_camera
from build_layout_json import build_layout_json


def store_name_to_id(name: str) -> str:
    return "STORE_" + name.upper().replace(" ", "_")


def process_store(
    store_dir: Path,
    api_key: str,
    model_name: str,
    open_time: str,
    close_time: str,
) -> Path:
    store_id = store_name_to_id(store_dir.name)
    print(f"\n{'='*60}")
    print(f"  {store_dir.name}  ->  {store_id}")
    print(f"{'='*60}")

    store_out = OUTPUT_DIR / store_dir.name
    frames_dir = store_out / "frames"

    # ── Step 1: Extract frames ──────────────────────────────────────
    print("\n[1/4] Extracting camera frames...")
    cameras = extract_frames(str(store_dir), str(frames_dir))
    if not cameras:
        raise RuntimeError(f"No camera videos found in {store_dir}")

    # ── Step 2: Parse floor plan ────────────────────────────────────
    layout_pngs = sorted(store_dir.glob("*.png")) + sorted(store_dir.glob("*.PNG"))
    if not layout_pngs:
        raise RuntimeError(f"No layout PNG found in {store_dir}")
    layout_png = layout_pngs[0]

    print(f"\n[2/4] Parsing floor plan: {layout_png.name}")
    layout_zones = parse_layout(str(layout_png), api_key, model_name)

    # ── Step 3: Calibrate cameras ───────────────────────────────────
    print("\n[3/4] Calibrating cameras against floor plan...")
    for i, cam in enumerate(cameras):
        if i > 0:
            time.sleep(5)  # stay within free-tier RPM limit between calls
        print(f"  -> {cam['camera_id']}  ({cam['camera_type']})")
        cam["calibration"] = calibrate_camera(
            frame_path=cam["frame_path"],
            layout_png_path=str(layout_png),
            layout_zones=layout_zones,
            camera_type=cam["camera_type"],
            frame_width=cam["frame_width"],
            frame_height=cam["frame_height"],
            api_key=api_key,
            model_name=model_name,
        )

    # ── Step 4: Build and save JSON ─────────────────────────────────
    print("\n[4/4] Building store_layout.json...")
    layout = build_layout_json(store_id, cameras, open_time, close_time)

    store_out.mkdir(parents=True, exist_ok=True)
    output_file = store_out / "store_layout.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(layout, f, indent=2)

    _print_summary(layout, output_file)
    return output_file


def _print_summary(layout: dict, output_file: Path) -> None:
    print(f"\n  Saved  : {output_file}")
    print(f"  Cameras: {len(layout['cameras'])}")
    for cam in layout["cameras"]:
        zone_names = [z["zone_name"] for z in cam["zones"]]
        print(f"    {cam['camera_id']:22s} ({cam['camera_type']:8s}) zones={zone_names}")
    if layout.get("billing_camera_id"):
        print(f"  Billing: {layout['billing_camera_id']}")
    if layout.get("entry_camera_id"):
        print(f"  Entry  : {layout['entry_camera_id']}")
    if layout.get("boh_zones"):
        print(f"  BOH    : {layout['boh_zones']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="L1 Store Layout Pipeline")
    parser.add_argument("--store", help="Process only this store folder name")
    parser.add_argument("--model", default="gemini-2.0-flash", help="Gemini model name")
    parser.add_argument("--open", default="10:00", dest="open_time")
    parser.add_argument("--close", default="22:00", dest="close_time")
    args = parser.parse_args()

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("[ERROR] OPENROUTER_API_KEY environment variable is not set.")
        sys.exit(1)

    if args.store:
        store_dirs = [INPUT_DIR / args.store]
        if not store_dirs[0].is_dir():
            print(f"[ERROR] Store directory not found: {store_dirs[0]}")
            sys.exit(1)
    else:
        store_dirs = sorted(d for d in INPUT_DIR.iterdir() if d.is_dir())

    if not store_dirs:
        print(f"[ERROR] No store directories found under {INPUT_DIR}")
        sys.exit(1)

    print(f"Stores to process: {[d.name for d in store_dirs]}")
    print(f"Model             : {args.model}")

    failed = []
    for store_dir in store_dirs:
        try:
            process_store(store_dir, api_key, args.model, args.open_time, args.close_time)
        except Exception as exc:
            print(f"\n[ERROR] {store_dir.name}: {exc}")
            traceback.print_exc()
            failed.append(store_dir.name)

    if failed:
        print(f"\n[WARN] Failed stores: {failed}")
        sys.exit(1)
    else:
        print("\n[DONE] All stores processed successfully.")


if __name__ == "__main__":
    main()
