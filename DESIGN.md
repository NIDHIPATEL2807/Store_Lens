# System Design — Purplle Store Analytics Pipeline

## Overview

A 7-layer computer vision pipeline that transforms raw CCTV footage into structured retail events served through a production REST API. The system is designed around one core principle: **adding a new store or camera requires zero code changes** — only annotation.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         INPUT LAYER                                      │
│   Raw CCTV mp4 files  (any resolution, any camera, any store)           │
└──────────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  L1 — Self-Annotation Tool  (CORE IDEATION)                             │
│  Interactive OpenCV tool: draw zone polygons directly on camera frames  │
│  → store_layout.json  (the single source of truth for all layers)       │
└──────────────────────────────────────────────────────────────────────────┘
                                   │
                    ┌──────────────┼──────────────┐
                    ▼              ▼              ▼
             Zone Cameras    Entry Camera    Billing Camera
                    │              │              │
                    ▼              ▼              ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  L2 — YOLOv8n + ByteTrack                                               │
│  Per-frame: bbox, centroid, track_id, confidence, occluded flag         │
│  Output: tracks.jsonl  (one per camera)                                 │
└──────────────────────────────────────────────────────────────────────────┘
              │                              │
    ┌─────────┘                             └───────────────────┐
    ▼                                                           ▼
┌──────────────────────┐                          ┌────────────────────────┐
│  L3 — Zone Assign    │                          │  L4 — Entry/Exit Gate  │
│  Shapely point-in-   │                          │  Zone-transition state │
│  polygon per centroid│                          │  machine:              │
│  ZONE_ENTER/EXIT/    │                          │  OUTSIDE→INSIDE=ENTRY  │
│  ZONE_DWELL          │                          │  INSIDE→OUTSIDE=EXIT   │
└──────────────────────┘                          └────────────────────────┘
              │                                                │
              └──────────────────┬─────────────────────────────┘
                                 │
                                 ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  L5 — Staff Detection + Cross-Camera Re-ID                              │
│  • HSV colour histogram → Groq VLM fallback → is_staff flag            │
│  • OSNet x0.25 (512-d embeddings) → cosine similarity → REENTRY        │
│  • YOLO person verifier: rejects backpacks/bags before staff check      │
└──────────────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  L6 — Event Merge (7 sequential passes)                                 │
│  1. Load + sort all events by timestamp                                 │
│  2. visitor_mapper: (camera_id, track_id) → visitor_id                 │
│  3. staff_applicator: retroactive is_staff from STAFF_FLAGGED          │
│  4. reentry_resolver: suppress duplicate ENTRY when REENTRY detected   │
│  5. pos_correlator: 5-min billing window → QUEUE_JOIN/ABANDON          │
│  6. session_sequencer: per-visitor_id seq counter (never resets)       │
│  7. event_builder: Pydantic v2 validation → events.jsonl               │
└──────────────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  L7 — FastAPI + SQLite (WAL mode)                                       │
│  POST /events/ingest  — batch, idempotent (INSERT OR IGNORE + rowcount) │
│  GET  /stores/{id}/metrics   — visitors, conversion, dwell, queue       │
│  GET  /stores/{id}/funnel    — 4-stage with drop-off %                 │
│  GET  /stores/{id}/heatmap   — zone scores 0–100                       │
│  GET  /stores/{id}/anomalies — spike / drop / dead-zone                │
│  GET  /health                — DB ping, stale feed check               │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Key Design Decisions

### 1. Self-Annotation Tool (L1) — biggest ideation

**What:** Rather than relying on pre-labeled datasets, external annotation services, or hardcoded pixel coordinates, the pipeline ships its own interactive annotation tool. A non-technical user opens a camera frame in a window and clicks polygon points to draw zone boundaries. The tool auto-saves `store_layout.json` — the spatial map that drives every downstream layer.

**Why this matters:** Every other component in the pipeline (detection, zone assignment, entry detection, Re-ID) is parameterized by `store_layout.json`. This means:
- Adding a new store = run the annotation tool for ~20 minutes, zero code changes
- Moving a camera = re-annotate that one camera, re-run from L2
- Changing zone boundaries = re-annotate, re-run from L3

Without this, the pipeline would be hardcoded to specific stores. The self-annotation tool is what makes the system a **general-purpose retail analytics platform**, not a one-off solution.

**Unique decisions inside L1:**
- Zones are drawn on **actual camera frames** extracted from the video at t=5s — not on a static floor plan. This means annotations are pixel-accurate for the actual camera perspective.
- Entry cameras get a **two-phase annotation**: first draw OUTSIDE/INSIDE zone polygons, then draw the entry threshold line. Both are needed for robust entry detection.
- The tool saves a **timestamped backup** before every overwrite — annotation work is never lost.
- Zone type (`shelf`, `entry`, `billing`, `floor`, `boh`) is captured at annotation time and flows through to every downstream event as `sku_zone` and `is_revenue_zone`.

---

### 2. Zone-based entry detection instead of line crossing

**What:** Entry/exit events fire on zone transitions (OUTSIDE-* → INSIDE-*) rather than geometric line crossings.

**Why:** Line crossing requires sub-pixel calibration and produces false positives when people pause at the door, walk parallel to the line, or when camera angles are oblique. Zone-based detection is spatially tolerant — large polygons absorb positional uncertainty.

**State machine:**
```
outside_store ──[ZONE_ENTER(INSIDE)]──► inside_store
inside_store  ──[ZONE_ENTER(DOOR)]───► exiting
exiting       ──[ZONE_ENTER(OUTSIDE)]─► outside_store  → fires EXIT
inside_store  ──[ZONE_ENTER(OUTSIDE)]─► outside_store  → fires EXIT
```

**Group detection pre-scan:** Before processing any events, a first pass clusters ZONE_ENTER(INSIDE) timestamps within a 4-second window. All cluster members get the same `group_id` before any event fires — solving the asymmetry where the first group member would otherwise have no `group_id`.

---

### 3. Two-stage staff detection

**What:** HSV colour histogram matching first. Groq VLM (`meta-llama/llama-4-scout-17b-16e-instruct`) as fallback for ambiguous crops. YOLO person verifier runs before either stage to reject non-person detections (bags, chairs).

**Why:** Pure colour matching fails in mixed lighting. Pure VLM is expensive (API latency + cost per call). The hybrid covers the common case cheaply and reserves the API call for genuinely ambiguous crops. The person verifier eliminates a whole class of false positives that would waste VLM calls.

---

### 4. OSNet x0.25 for cross-camera Re-ID

**What:** 512-dimensional appearance embeddings per person crop. Cosine similarity matching at 0.70 threshold across cameras.

**Why:** OSNet was specifically designed for person re-identification. x0.25 is the smallest variant — runs on CPU in ~20ms/crop. The 512-d space provides enough discriminative power for a small store population. Shape-guarded comparisons prevent the numpy mismatch that occurs when crop extraction fails and returns None.

---

### 5. SQLite WAL mode for the API database

**What:** Single file `data/store.db` in WAL (Write-Ahead Logging) journal mode. `INSERT OR IGNORE` + `rowcount` check for idempotent ingest.

**Why:** Zero-config, no Docker service dependency, no credentials. WAL mode enables concurrent reads + writes without blocking — critical for an API that ingests and serves simultaneously. The `rowcount` check correctly returns `accepted=0` for duplicate event IDs (pure `INSERT OR IGNORE` without rowcount would wrongly count ignored rows).

---

### 6. Camera-agnostic event schema

All events from L3 through L7 share one schema:

```json
{
  "event_id":   "uuid-v4",
  "store_id":   "STORE_STORE_1",
  "camera_id":  "CAM_ZONE_01",
  "visitor_id": "VIS_c8a2f1",
  "event_type": "ZONE_ENTER",
  "timestamp":  "2026-03-08T10:05:21.400Z",
  "zone_id":    "AQUALOGICA",
  "dwell_ms":   0,
  "is_staff":   false,
  "confidence": 0.87,
  "converted":  false,
  "metadata": {
    "queue_depth":  null,
    "sku_zone":     "Aqualogica",
    "session_seq":  3,
    "group_id":     null,
    "group_size":   1,
    "track_id":     7
  }
}
```

This schema is validated by Pydantic v2 at the L6 boundary. Events that fail validation are written to `rejected_events.jsonl` with the reason, never silently dropped.

---

## Event types produced

| Event | Source | Meaning |
|---|---|---|
| `ZONE_ENTER` | L3 | Centroid entered zone polygon |
| `ZONE_EXIT` | L3 | Centroid left zone polygon |
| `ZONE_DWELL` | L3 | Present in zone 30+ seconds |
| `ENTRY` | L4 | Crossed gate OUTSIDE → INSIDE |
| `EXIT` | L4 | Crossed gate INSIDE → OUTSIDE |
| `REENTRY` | L5 | Known visitor matched via Re-ID |
| `VISITOR_LINKED` | L5 | Same person seen on different camera |
| `STAFF_FLAGGED` | L5 | Track classified as store staff |
| `BILLING_QUEUE_JOIN` | L6 | Reached billing zone |
| `BILLING_QUEUE_ABANDON` | L6 | Left billing zone before converting |

---

## Partial-camera coverage handling

Store 1's entry camera covered a side entrance with no foot traffic during the recording. The pipeline handles this correctly: visitors who first appear in zone cameras get an ENTRY event generated by L5 (first-track-appearance = entry signal). This is the correct behaviour — it is not a fallback or a bug. Stores with partial camera coverage are a real-world constraint, and the pipeline degrades gracefully.

---

## AI-Assisted Decisions

This project used Claude (Anthropic) as a development assistant for:

1. **Zone-transition entry logic** — Original line-crossing approach produced false positives. Claude helped design the OUTSIDE/INSIDE zone state machine as a more robust alternative.

2. **Group detection pre-scan** — First design left the first group member without a `group_id`. Claude identified the asymmetry and proposed the two-pass approach.

3. **Re-ID shape guard** — None crops stored as `np.zeros(1)` caused shape mismatch against 512-d OSNet embeddings. Claude identified the fix: skip cache entirely when embedding is None.

4. **FastAPI migration** — Rewrote all Flask Blueprints to FastAPI APIRouters, fixed test fixtures for `TestClient`, handled invalid-JSON → 400 edge case.

5. **SQLite thread safety in tests** — Tests returned 503 because the shared in-memory connection was created in the test thread and used by the app thread. Fix: `check_same_thread=False`.

6. **Idempotent ingest rowcount** — `INSERT OR IGNORE` without checking `cursor.rowcount` counted ignored duplicates as accepted. Fix: `if cur.rowcount > 0: accepted += 1`.
