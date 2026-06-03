# Purplle Store Analytics Pipeline

Converts raw CCTV footage into structured zone-dwell events ready for the analytics API.

```
Videos (mp4)  ──►  L1 (annotate)  ──►  store_layout.json
                                               │
Videos (mp4)  ──►  L2 (detect)    ──►  tracks.jsonl  ──►  L3 (zones)  ──►  zone_events.jsonl
```

---

## Layers at a glance

| Layer | Folder | What it does | Input | Output |
|---|---|---|---|---|
| **L1** | `L1_StoreLayout_selfannotate/` | You draw zone polygons on camera frames with your mouse. Produces the spatial map every other layer reads. | Raw mp4 videos | `store_layout.json` |
| **L2** | `L2_Detect/` | Runs YOLOv8 person detection + ByteTrack on every video. One JSONL per camera per clip — a row per processed frame with bboxes, centroids, track IDs. | mp4 video + `store_layout.json` | `tracks.jsonl` |
| **L3** | `L3_ZoneAssign/` | Pure geometry — finds which shelf/zone each track centroid is in each frame. Emits ZONE_ENTER, ZONE_EXIT, ZONE_DWELL events with dwell times. | `tracks.jsonl` + `store_layout.json` | `zone_events.jsonl` |

---

## Setup (once)

```cmd
cd pipeline
python -m venv venv
venv\Scripts\activate
pip install -r L1_StoreLayout_selfannotate\requirements.txt
pip install -r L2_Detect\requirements.txt
pip install shapely
```

---

## Run commands

### L1 — Store Layout Annotation

| Command | What it does |
|---|---|
| `python run_annotation.py` | Annotate all stores found in `input/` |
| `python run_annotation.py --store "Store 1"` | Annotate one store only |
| `python run_annotation.py --open 09:00 --close 21:00` | Set custom opening hours |
| `python visualize_zones.py` | Save annotated preview images to `output/<Store>/viz/` |
| `python visualize_zones.py --show` | Same but also pop open interactively |

> Run from: `pipeline/L1_StoreLayout_selfannotate/`

**Output:** `output/Store 1/store_layout.json`

---

### L2 — Person Detection & Tracking

| Command | What it does |
|---|---|
| `python run_l2.py --layout <layout.json> --video <video.mp4> --clip_start 2026-03-08T10:00:00Z` | Process one video, with explicit clip start time |
| `python run_l2.py --layout <layout.json> --video_dir <folder/>` | Process all videos in a folder (clip start parsed from filename) |
| `python run_l2.py --layout <layout.json> --video_dir <folder/> --frame_skip 5 --model yolov8s.pt` | Override frame skip and model weights |
| `python run_l2.py --layout <layout.json> --video <video.mp4> --camera_id CAM_ZONE_01` | Force-assign video to a specific camera ID |

> Run from: `pipeline/L2_Detect/`

**Output:** `output/<store_id>/<camera_id>__<video_stem>.jsonl`

Each line is one processed frame:
```json
{
  "frame": 42,
  "timestamp": "2026-03-08T10:00:01.400Z",
  "camera_id": "CAM_ZONE_01",
  "store_id": "STORE_1",
  "tracks": [
    {
      "track_id": 3,
      "bbox": [120, 300, 280, 680],
      "centroid": [200, 490],
      "confidence": 0.84,
      "bbox_area": 57600,
      "occluded": false,
      "long_duration_flag": false
    }
  ],
  "frame_meta": { "total_detections": 1, "dropped": false, "processing_ms": 52 }
}
```

#### L2 Visualizer

| Command | What it does |
|---|---|
| `python visualize.py --jsonl <tracks.jsonl> --video <video.mp4>` | Live window — coloured boxes, track IDs, centroids, timestamps |
| `python visualize.py --jsonl <tracks.jsonl> --video <video.mp4> --speed 2.0` | Same at 2× speed |
| `python visualize.py --jsonl <tracks.jsonl> --video <video.mp4> --out review.mp4` | Save annotated video instead of live window |

> Run from: `pipeline/L2_Detect/`

---

### L3 — Zone Assignment

| Command | What it does |
|---|---|
| `python run_l3.py --tracks <tracks.jsonl> --layout <layout.json> --output <events.jsonl>` | Process one tracks file |
| `python run_l3.py --tracks_dir <folder/> --layout <layout.json> --output_dir output/` | Process all `.jsonl` files in a folder in one shot |

> Run from: `pipeline/L3_ZoneAssign/`

**Output:** `output/<camera_id>_zone_events.jsonl`

Each line is one zone event:
```json
{
  "event_id": "a3f2...",
  "store_id": "STORE_1",
  "camera_id": "CAM_ZONE_01",
  "visitor_id": null,
  "event_type": "ZONE_ENTER",
  "timestamp": "2026-03-08T10:00:05.200Z",
  "zone_id": "AQUALOGICA",
  "dwell_ms": 0,
  "is_staff": null,
  "confidence": 0.87,
  "metadata": {
    "queue_depth": null,
    "sku_zone": "Aqualogica",
    "is_revenue_zone": true,
    "session_seq": 3,
    "track_id": 2
  }
}
```

Event types: `ZONE_ENTER` · `ZONE_EXIT` · `ZONE_DWELL` (fires every 30 s of continuous presence)

---

## Full pipeline — Store 1 example

```cmd
:: Step 1 — annotate store layout (one time)
cd L1_StoreLayout_selfannotate
python run_annotation.py --store "Store 1"

:: Step 2 — detect and track all cameras
cd ..\L2_Detect
python run_l2.py ^
  --layout "..\L1_StoreLayout_selfannotate\output\Store 1\store_layout.json" ^
  --video_dir "..\L1_StoreLayout_selfannotate\input\Store 1\"

:: Step 3 — assign zones to all tracks
cd ..\L3_ZoneAssign
python run_l3.py ^
  --tracks_dir "..\L2_Detect\output\STORE_STORE_1\" ^
  --layout     "..\L1_StoreLayout_selfannotate\output\Store 1\store_layout.json" ^
  --output_dir "output\"
```

---

## Data flow

```
input/Store 1/
├── CAM 1 - zone.mp4   ─┐
├── CAM 2 - zone.mp4   ─┤──► L1 run_annotation.py
├── CAM 3 - entry.mp4  ─┤
└── CAM 5 - billing.mp4─┘
                              │
                              ▼
                    store_layout.json   ◄─── spatial map (zones, polygons, camera metadata)
                              │
              ┌───────────────┘
              │               │
              ▼               ▼
       mp4 videos      store_layout.json
              │               │
              └──────┬────────┘
                     ▼
              L2 run_l2.py
                     │
                     ▼
         tracks.jsonl (one per camera)
         └── frame / timestamp / track_id / bbox / centroid / occluded
                     │
              ┌──────┘
              │        store_layout.json
              └──────┬────────────────────
                     ▼
              L3 run_l3.py
                     │
                     ▼
         zone_events.jsonl (one per camera)
         └── ZONE_ENTER / ZONE_EXIT / ZONE_DWELL
             with zone_id / dwell_ms / track_id / timestamp
```

---

## What feeds what

| Produces | Consumed by |
|---|---|
| `store_layout.json` (L1) | L2 — camera config, frame dimensions, zone polygons |
| `store_layout.json` (L1) | L3 — zone polygons for point-in-polygon matching |
| `tracks.jsonl` (L2) | L3 — centroid + track_id per frame |
| `tracks.jsonl` (L2) | L5 — bbox crops for uniform detection + OSNet Re-ID |
| `zone_events.jsonl` (L3) | L6 — zone dwell data to join with visitor sessions |

---

## Known gaps (before running L4+)

| Issue | Layer | Fix needed |
|---|---|---|
| `entry_line` and `entry_direction` missing from `store_layout.json` | L1 | Re-annotate entry camera in L1 — the line-crossing tool adds these fields |
| Entry zone polygon drawn in wrong screen position | L1 | Re-annotate the ENTRY polygon to cover where people actually appear |
| Entry camera 78% detections flagged `occluded` | L2 | Occlusion threshold (200k px²) is designed for mid-floor cameras; entry cameras need a higher value |
