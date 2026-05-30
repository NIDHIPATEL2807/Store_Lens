# Retail Vision Pipeline

Processes retail store camera footage and converts raw video into structured business events — entries, zone visits, dwell times, billing queues, exits — written as JSONL.

---

## How It Works (End to End)

```
INPUT/*.mp4
     │
     ▼
┌─────────────┐     ┌──────────────┐     ┌─────────────────────┐     ┌──────────────┐
│  detect.py  │────▶│   track.py   │────▶│  Event State Machine │────▶│ OUTPUT/*.jsonl│
│  YOLOv8     │     │  ByteTrack   │     │  (in main.py)        │     │              │
│  finds people│    │  assigns IDs │     │  decides what matters│     │              │
└─────────────┘     └──────────────┘     └─────────────────────┘     └──────────────┘
```

**Step 1 — detect.py** reads every frame of the video and runs YOLOv8 on it. YOLOv8 finds every person in the frame and returns bounding boxes `[x1, y1, x2, y2]` plus a raw confidence score. Nothing else — no IDs, no memory of previous frames.

**Step 2 — track.py** takes those per-frame detections and feeds them into ByteTrack. ByteTrack uses IoU (overlap) between frames to decide "the box at frame 100 and the box at frame 101 are the same person." It assigns each person a stable `track_id` (e.g. `7`) that persists until the person leaves the frame. The same number refers to the same physical person across hundreds of frames.

**Step 3 — main.py** watches what each track is doing. It knows the camera type (entry/floor/billing), the zone layout, and it maintains a state machine per person. It only emits an event when something business-meaningful happens — not "person seen at frame 312" but "visitor entered the SKINCARE zone."

---

## File Structure

```
model/
├── detect.py          # YOLOv8 wrapper — detects people per frame
├── track.py           # ByteTrack wrapper — assigns stable IDs across frames
├── main.py            # Pipeline orchestrator + event state machine
├── config.json        # Store config: zones, thresholds, camera IDs
├── requirements.txt   # Python dependencies
├── INPUT/             # Drop your .mp4 / .avi / .mov files here
├── OUTPUT/            # JSONL event files written here after processing
└── venv/              # Python virtual environment
```

---

## Setup

```powershell
cd model
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

On first run, `ultralytics` will automatically download the YOLOv8 nano weights (`yolov8n.pt`, ~6 MB). Subsequent runs use the cached file.

---

## Running

```powershell
# 1. Activate the venv
.\venv\Scripts\Activate.ps1

# 2. Drop video files into INPUT/
#    Name them so the camera type is clear:
#      anything with "entry"   → entry camera
#      anything with "billing" → billing camera
#      everything else         → floor camera
#
#    Examples:
#      CAM_ENTRY_01.mp4
#      store_billing_cam.mp4
#      CAM_FLOOR_01.mp4
#      CAM 1.mp4              ← treated as floor

# 3. Run
python main.py

# 4. Results land in OUTPUT/
#    One .jsonl file per input video.
#    Each line is one JSON event.
```

---

## Camera Types and What They Track

The pipeline infers camera type from the video filename by looking for keywords.

| Keyword in filename | Camera type | What it tracks |
|---|---|---|
| `entry`, `door`, `entrance` | **entry** | ENTRY, EXIT, REENTRY |
| `billing`, `checkout`, `cashier` | **billing** | BILLING_QUEUE_JOIN |
| anything else | **floor** | ZONE_ENTER, ZONE_DWELL |

---

## Zone Configuration (config.json)

Zones are polygons defined in **normalised 0–1 coordinates** (so they scale to any resolution automatically).

```json
"floor": {
  "camera_id": "CAM_FLOOR_01",
  "zones": {
    "SKINCARE": {
      "polygon": [[0.0, 0.0], [0.5, 0.0], [0.5, 0.5], [0.0, 0.5]],
      "sku_zone": "MOISTURISER"
    }
  }
}
```

Each coordinate `[x, y]` is a fraction of frame width/height. The example above covers the top-left quadrant of the frame.

To customise zones for your actual camera layout: pause the video in any player, note where the shelves/zones are as fractions of the frame, and update the polygon points accordingly.

**Default zone layout (floor cam — split into 4 quadrants):**

```
┌──────────┬──────────┐
│ SKINCARE │ LIPSTICK │
│          │          │
├──────────┼──────────┤
│FRAGRANCE │ HAIRCARE │
│          │          │
└──────────┴──────────┘
```

---

## Events Emitted

Every event written to the JSONL file has this structure:

```json
{
  "event_id":   "a3f2c1d4-...",
  "store_id":   "STORE_BLR_002",
  "camera_id":  "CAM_ENTRY_01",
  "visitor_id": "VIS_c8a2f1",
  "event_type": "ENTRY",
  "timestamp":  "2026-03-03T14:22:10Z",
  "zone_id":    null,
  "dwell_ms":   0,
  "is_staff":   false,
  "confidence": 0.87,
  "metadata": {
    "queue_depth": null,
    "sku_zone":    null,
    "session_seq": 1
  }
}
```

### Event types

| Event | Camera | When it fires |
|---|---|---|
| `ENTRY` | entry | A new person track appears at the door |
| `EXIT` | entry | A tracked person disappears from the entry cam |
| `REENTRY` | entry | Someone who exited comes back within 90 seconds |
| `ZONE_ENTER` | floor | A person's centroid crosses into a zone polygon |
| `ZONE_DWELL` | floor | Person has been in the same zone for 30+ seconds (repeats every 30 s) |
| `BILLING_QUEUE_JOIN` | billing | Person enters the billing zone; `queue_depth` = how many people are already there |

### Field rules (enforced by the code)

| Field | Rule |
|---|---|
| `session_seq` | Increments by 1 for each event per visitor per session. Resets to 1 on REENTRY. |
| `dwell_ms` | Only nonzero on `ZONE_DWELL`. Measures total time in zone since `ZONE_ENTER`. |
| `zone_id` | `null` on ENTRY / EXIT / REENTRY. Populated for all other events. |
| `queue_depth` | Only populated on `BILLING_QUEUE_JOIN`. `null` everywhere else. |
| `confidence` | Raw YOLO score from that frame — never rounded, never filtered after threshold. |
| `is_staff` | `true` if the visitor has been visible continuously for ≥ 10 minutes. Does NOT change the ID format — staff visitor IDs still use the `VIS_` prefix. |
| `visitor_id` | Stable short hash of `camera_id + ByteTrack_ID`. Same person → same ID within a session. |

---

## How REENTRY Works

When a track disappears from the entry camera, an `EXIT` event fires and the visitor is held in a short-term memory for 90 seconds.

If a new track appears at the same entry camera within those 90 seconds, it is matched to the most recent exited visitor and gets their `visitor_id` back — but `session_seq` resets to 1.

This works well for single-entrance stores. The assumption is that if someone left and came back quickly, they are the same person.

---

## How Staff Detection Works

There is no uniform classifier. Staff are identified by **dwell time heuristic**: if a track has been continuously visible on camera for more than `staff_threshold_minutes` (default 10 minutes), the `is_staff` flag is set to `true` on all subsequent events for that track.

Configurable in `config.json`:
```json
"staff_threshold_minutes": 10
```

---

## Configurable Thresholds

All in `config.json`:

| Key | Default | What it controls |
|---|---|---|
| `confidence_threshold` | `0.4` | Minimum YOLO score to accept a detection |
| `dwell_threshold_seconds` | `30` | How long someone must stand still before a `ZONE_DWELL` fires |
| `reentry_window_seconds` | `90` | Window after EXIT to look for a returning visitor |
| `staff_threshold_minutes` | `10` | Continuous visibility before marking as staff |
| `device` | `"cpu"` | Change to `"cuda"` if a GPU is available |
| `model_path` | `"yolov8n.pt"` | Swap for `yolov8s.pt` / `yolov8m.pt` for higher accuracy (slower) |

---

## Output Example

Running against a floor cam video produces a file like `OUTPUT/CAM_FLOOR_01.jsonl`:

```jsonl
{"event_id": "b9e1a2f3-...", "store_id": "STORE_BLR_002", "camera_id": "CAM_FLOOR_01", "visitor_id": "VIS_c8a2f1", "event_type": "ZONE_ENTER", "timestamp": "2026-03-03T14:22:45Z", "zone_id": "SKINCARE", "dwell_ms": 0, "is_staff": false, "confidence": 0.91, "metadata": {"queue_depth": null, "sku_zone": "MOISTURISER", "session_seq": 2}}
{"event_id": "c7d3b4e5-...", "store_id": "STORE_BLR_002", "camera_id": "CAM_FLOOR_01", "visitor_id": "VIS_c8a2f1", "event_type": "ZONE_DWELL", "timestamp": "2026-03-03T14:23:15Z", "zone_id": "SKINCARE", "dwell_ms": 30000, "is_staff": false, "confidence": 0.89, "metadata": {"queue_depth": null, "sku_zone": "MOISTURISER", "session_seq": 3}}
```

Each line is a valid JSON object. You can pipe it directly into any analytics tool, database, or stream processor.

---

## Dependencies

| Package | Purpose |
|---|---|
| `ultralytics` | YOLOv8 model — person detection |
| `supervision` | ByteTrack implementation — cross-frame tracking |
| `opencv-python` | Reading video files frame by frame |
| `numpy` | Array operations for bounding boxes |
