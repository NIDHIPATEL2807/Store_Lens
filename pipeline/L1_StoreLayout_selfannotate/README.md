# Layer 1 — Store Layout Builder

Extracts one representative frame per camera from the store's CCTV footage, then lets you **draw zone polygons directly on the camera image with your mouse**. The result is a `store_layout.json` that every downstream layer (person detection, tracking, re-ID, dwell-time) reads to understand the store's physical space.

---

## Why this exists

Downstream layers need to know *where* things are in each camera frame — which pixels are the cash counter, which pixels are the haircare shelf, where the entry threshold is. Without this spatial map, detection and tracking results have no business meaning.

Layer 1 produces that map once per store. It never needs to run again unless cameras move or the store is re-arranged.

---

## Folder structure

```
L1_StoreLayout_selfannotate/
├── run_annotation.py      # entry point — run this
├── annotate_zones.py      # interactive OpenCV annotation tool
├── extract_frames.py      # pulls one frame per camera from video
├── build_layout_json.py   # assembles the final JSON structure
├── visualize_zones.py     # overlays polygons on frames to verify
├── requirements.txt
├── input/
│   ├── Store 1/
│   │   ├── CAM 1 - zone.mp4
│   │   ├── CAM 2 - zone.mp4
│   │   ├── CAM 3 - entry.mp4
│   │   ├── CAM 5 - billing.mp4
│   │   └── Store 1 - layout.png
│   └── Store 2/
│       └── ...
└── output/
    └── Store 1/
        ├── frames/          # extracted camera frames (auto-generated)
        ├── viz/             # annotated preview images (from visualize_zones.py)
        └── store_layout.json
```

Input convention: video filenames must contain `zone`, `entry`, or `billing` so the camera type is auto-detected. Any `.mp4 / .mov / .mkv` works, including HEVC-encoded files.

---

## Setup

```cmd
cd pipeline
venv\Scripts\activate
pip install -r L1_StoreLayout_selfannotate\requirements.txt
```

---

## How to run

### Step 1 — Annotate all stores

```cmd
cd L1_StoreLayout_selfannotate
python run_annotation.py
```

This will:
1. Extract one frame at t=5s from every camera video
2. Open an annotation window for each camera — you draw the zones
3. Auto-save `store_layout.json` after every camera (progress is never lost)

To process a single store:
```cmd
python run_annotation.py --store "Store 1"
```

To set custom opening hours:
```cmd
python run_annotation.py --open 09:00 --close 21:00
```

---

### Step 2 — Draw zones in the annotation window

When the window opens you see the actual camera frame. The tool loops through any pre-existing zones and lets you redraw them. If there are none yet, press `A` to start adding.

| Key | Action |
|---|---|
| Left click | Place a polygon point |
| Right click | Undo the last point |
| `ENTER` | Confirm the current polygon (needs ≥ 3 points) |
| `A` | Add a brand-new zone — prompts for name + type in the terminal |
| `D` | Delete the last confirmed zone |
| `N` | Skip this zone — keep its existing polygon unchanged |
| `R` | Reset / clear points and start the zone again |
| `Z` | Toggle zoom inset under the cursor (for precise point placement) |
| `S` | Save all zones for this camera and close the window |
| `Q` | Quit this camera without saving |

Zone types: `shelf` · `billing` · `entry` · `boh` · `floor`

For **entry cameras**, after zone annotation a second window opens to draw the entry threshold line (2 clicks). Press `D` to cycle the crossing direction (`bottom_to_top`, `left_to_right`, etc.).

---

### Step 3 — Verify (optional)

```cmd
python visualize_zones.py
```

Saves annotated preview images to `output/<StoreName>/viz/`. Each image shows the zone polygons overlaid on the camera frame with colour-coded fills and labels.

To pop them open interactively:
```cmd
python visualize_zones.py --show
```

---

## Output — `store_layout.json`

```json
{
  "store_id": "STORE_1",
  "open_hours": { "open": "10:00", "close": "22:00" },
  "cameras": [
    {
      "camera_id": "CAM_ZONE_01",
      "camera_type": "zone",
      "frame_width": 1920,
      "frame_height": 1080,
      "zones": [
        {
          "zone_id": "LEFT_SHELF",
          "zone_name": "Haircare Wall",
          "zone_type": "shelf",
          "is_revenue_zone": true,
          "polygon": [[50,100],[480,100],[480,900],[50,900]]
        }
      ]
    },
    {
      "camera_id": "CAM_ENTRY_01",
      "camera_type": "entry",
      "entry_line": { "x1": 960, "y1": 0, "x2": 960, "y2": 1080 },
      "entry_direction": "bottom_to_top",
      "zones": [...]
    }
  ],
  "boh_zones": ["BOH"],
  "billing_camera_id": "CAM_BILLING_01",
  "entry_camera_id": "CAM_ENTRY_01"
}
```

This file is the **single source of truth** consumed by all downstream layers.

---

## Re-annotating

Run `python run_annotation.py` again at any time. If `store_layout.json` already exists, the tool loads it and shows the existing polygons in yellow so you can refine them or add new zones. A timestamped backup is saved automatically before any overwrite.
