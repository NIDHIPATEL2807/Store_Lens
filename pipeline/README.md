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
| **L4** | `L4_entry/` | Watches the entry camera and detects threshold crossings using the `entry_line` from L1. Assigns a deterministic `visitor_id` per session and emits ENTRY / EXIT events. Handles group entry, debounced crossings, glass-door reflections, and born-inside tracks. | `tracks.jsonl` (entry cam) + `store_layout.json` | `entry_events.jsonl` |
| **L5** | `L5_reid/` | Staff detection (HSV uniform colour → Groq VLM fallback) + Re-ID (OSNet appearance embeddings, cross-camera linking). Saves staff crop images. | `tracks.jsonl` (all cams) + L4 entry events + source videos | `reid_events.jsonl` + `staff_crops/` |
| **L6** | `L6_emit/` | Merge layer — joins L3/L4/L5 outputs into one clean event stream. Fills visitor_ids on zone events, applies is_staff retroactively, handles REENTRY dedup, POS correlation, session_seq numbering, schema validation. | L3+L4+L5 JSONL + `pos_transactions.csv` + `store_layout.json` | `events.jsonl` + `rejected_events.jsonl` |
| **L7** | `L7_api/` | Flask REST API — ingests L6 events into SQLite, serves live metrics, conversion funnel, zone heatmap, anomaly detection, and health endpoint. | `events.jsonl` (POST) | HTTP API on port 8000 |

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

### L4 — Entry / Exit Detection

| Command | What it does |
|---|---|
| `python run_l4.py --tracks <entry_tracks.jsonl> --layout <layout.json> --output <events.jsonl>` | Process one entry-camera tracks file |
| `python run_l4.py --tracks_dir <folder/> --layout <layout.json> --output_dir output/` | Process all `.jsonl` in a folder (skips non-entry cameras automatically) |
| `python run_l4.py --tracks ... --clip_start_utc "2026-03-08T10:00:00Z"` | Pin the date used for deterministic visitor IDs |

> Run from: `pipeline/L4_entry/`

**Output:** `output/<tracks_stem>_entry_events.jsonl`

Each line is one entry/exit event:
```json
{
  "event_id": "uuid-v4",
  "store_id": "STORE_STORE_1",
  "camera_id": "CAM_ENTRY_01",
  "visitor_id": "VIS_c8a2f1",
  "event_type": "ENTRY",
  "timestamp": "2026-03-08T10:00:05.200Z",
  "zone_id": "ENTRY_ZONE",
  "dwell_ms": 0,
  "is_staff": null,
  "confidence": 0.84,
  "metadata": {
    "queue_depth": null,
    "sku_zone": null,
    "session_seq": 1,
    "group_id": "G_4a2f11",
    "group_size": 2,
    "track_id": 3
  }
}
```

Event types: `ENTRY` · `EXIT`

Edge cases handled:
- **Debounce** — side must be stable for 2 consecutive frames before committing a crossing
- **Re-entry suppression** — same `track_id` cannot emit ENTRY again within 30 s
- **Group entry** — multiple ENTRYs within 2 s share a `group_id`
- **Born inside** — track appearing for first time inside the store triggers ENTRY immediately (covers ByteTrack loss mid-crossing)
- **Glass-door reflection** — Store 2: detections still outside the line with confidence < 0.45 are skipped

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
| `entry_events.jsonl` (L4) | L6 — ENTRY/EXIT events carrying `visitor_id` to anchor each session |

---

---

### L5 — Staff Detection + Re-ID

| Command | What it does |
|---|---|
| `python run_l5.py --tracks_dir <L2_output_folder> --entry_events <entry_events.jsonl> --videos_dir <videos_folder> --layout <layout.json> --output_dir output/` | Process all cameras — staff detection + cross-camera Re-ID |

> Run from: `pipeline/L5_reid/`  
> Install: `pip install -r requirements.txt`

**Output:** `output/reid_events.jsonl` + `output/staff_crops/*.jpg`

**Visualize:**
```cmd
python visualize.py --events output/reid_events.jsonl --tracks <L2_tracks.jsonl> --video <video.mp4> --crops output/staff_crops
```

---

### L6 — Event Emitter

| Command | What it does |
|---|---|
| `python run_l6.py --zone_events <zone_events.jsonl> --entry_events <entry_events.jsonl> --reid_events <reid_events.jsonl> --layout <layout.json> --output_dir output/` | Merge all events + POS correlation into final schema |
| Add `--pos_data pos_transactions.csv` | Enable conversion tracking |

> Run from: `pipeline/L6_emit/`  
> Install: `pip install pydantic`

**Output:** `output/events.jsonl` (clean, API-ready) + `output/rejected_events.jsonl` (validation failures with reason)

---

### L7 — Intelligence API

| Command | What it does |
|---|---|
| `python app/main.py` | Start Flask API on port 8000 |
| `flask --app app.main run --port 8000` | Alternative start via Flask CLI |
| `pytest tests/ -v` | Run all tests |
| `python feed_events.py --events ../L6_emit/output/events.jsonl` | POST L6 events to the API |

> Run from: `pipeline/L7_api/`  
> Install: `pip install -r requirements.txt`

**Endpoints:**

| Endpoint | Method | What it returns |
|---|---|---|
| `/events/ingest` | POST | Accepts batch of events (up to 500). Idempotent. Returns 200/207/400. |
| `/stores/{id}/metrics` | GET | Unique visitors, conversion rate, avg dwell per zone, queue depth, abandonment rate |
| `/stores/{id}/funnel` | GET | Entry → Zone → Billing → Purchase with drop-off % at each stage |
| `/stores/{id}/heatmap` | GET | Zone visit frequency normalised 0–100 score |
| `/stores/{id}/anomalies` | GET | Active anomalies: queue spike, conversion drop, dead zone |
| `/health` | GET | DB status, uptime, per-store last event + stale feed warning |

**Feed events from L6:**
```cmd
cd pipeline\L7_api
python -c "
import json, requests
with open('../L6_emit/output/events.jsonl') as f:
    batch = [json.loads(l) for l in f if l.strip()]
r = requests.post('http://localhost:8000/events/ingest', json=batch[:500])
print(r.json())
"
```

---

## Known gaps (before running L5+)

| Issue | Layer | Fix needed |
|---|---|---|
| Entry camera 78% detections flagged `occluded` | L2 | Occlusion threshold (200k px²) is tuned for mid-floor cameras; entry cameras see full-body silhouettes that legitimately exceed it. Needs a per-camera-type threshold in `detect_track.py`. |
| `visitor_id` is track-scoped, not person-scoped | L4 | If ByteTrack assigns a new track_id to the same person after a gap, they get a different visitor_id. L5 Re-ID will unify these. |
