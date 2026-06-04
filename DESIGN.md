# System Design — Purplle Store Analytics Pipeline

## Overview

A 7-layer computer vision pipeline that transforms raw CCTV footage into structured retail events and serves them through a REST API. The system is designed to be camera-agnostic: adding a new store requires only annotating zones in L1 — no code changes.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         INPUT LAYER                                      │
│   Raw CCTV mp4 files  +  Manual zone annotation (L1)                    │
│   → store_layout.json  (zone polygons, entry lines, camera metadata)     │
└──────────────────────────────────────────────────────────────────────────┘
                                   │
                    ┌──────────────┼──────────────┐
                    ▼              ▼              ▼
             Zone Cameras    Entry Camera    Billing Camera
                    │              │              │
                    ▼              ▼              ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  L2 — YOLOv8 + ByteTrack                                                │
│  Per-frame: bbox, centroid, track_id, confidence, occluded flag          │
│  Output: tracks.jsonl  (one per camera)                                 │
└──────────────────────────────────────────────────────────────────────────┘
                    │                    │
          ┌─────────┘                    └─────────────────────────┐
          ▼                                                         ▼
┌──────────────────────┐                                ┌──────────────────────┐
│  L3 — Zone Assign    │                                │  L4 — Entry/Exit     │
│  Shapely PIP per     │                                │  Zone-transition     │
│  frame centroid      │                                │  state machine       │
│  → ZONE_ENTER/EXIT/  │                                │  OUTSIDE→INSIDE=     │
│    ZONE_DWELL        │                                │  ENTRY               │
└──────────────────────┘                                └──────────────────────┘
          │                                                         │
          └─────────────────────┬───────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  L5 — Staff Detection + Cross-Camera Re-ID                               │
│  • Uniform colour HSV → Groq VLM fallback → is_staff flag                │
│  • OSNet x0.25 (512-d embeddings) → cosine similarity → REENTRY/VISITOR_LINKED │
└──────────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  L6 — Event Merge (run_l6.py)                                            │
│  1. Load + sort L3 / L4 / L5 events by timestamp                        │
│  2. visitor_mapper: (camera_id, track_id) → visitor_id from L4+L5       │
│  3. staff_applicator: retroactive is_staff from L5 STAFF_FLAGGED        │
│  4. reentry_resolver: suppress duplicate L4 ENTRY when L5 REENTRY found │
│  5. pos_correlator: 5-min billing window → BILLING_QUEUE_JOIN/ABANDON    │
│  6. session_sequencer: per-visitor_id seq counter (never resets)         │
│  7. event_builder: Pydantic validation → events.jsonl / rejected.jsonl  │
└──────────────────────────────────────────────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  L7 — FastAPI + SQLite                                                   │
│  POST /events/ingest  (batch, idempotent, INSERT OR IGNORE)             │
│  GET  /stores/{id}/metrics   — visitors, conversion, dwell, queue        │
│  GET  /stores/{id}/funnel    — 4-stage with drop-off %                  │
│  GET  /stores/{id}/heatmap   — zone scores 0–100                        │
│  GET  /stores/{id}/anomalies — spike / drop / dead-zone                 │
│  GET  /health                — DB ping, stale feed check                │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## Key Design Decisions

### 1. Zone-based entry detection (L4)

**What:** Entry/exit is detected by watching zone transitions (OUTSIDE-* → INSIDE-*) rather than line crossing.

**Why:** Line crossing approaches require sub-pixel calibration and produce false positives when people pause at the door, move parallel to the line, or when the camera angle is not perpendicular. Zone-based detection is spatially tolerant — the "INSIDE" polygon is large enough that transient positions don't trigger false events.

**Trade-off:** Requires manual annotation of OUTSIDE/INSIDE zones per entry camera. Pays off because the detection is robust across different camera heights and angles.

---

### 2. Two-stage staff detection (L5)

**What:** First attempt uniform colour detection via HSV histogram matching. If inconclusive, send the crop to Groq's LLaMA vision model for a yes/no judgement.

**Why:** Pure colour matching fails in mixed lighting. Pure VLM is expensive (API latency + cost). The hybrid approach covers >90% of cases with colour matching and only calls the API for ambiguous crops.

**Trade-off:** Groq API dependency for edge cases. If API is down, colour-match-only mode still runs (soft fallback).

---

### 3. OSNet for Re-ID embeddings

**What:** OSNet x0.25 (512-dimensional appearance embeddings) for cross-camera visitor matching.

**Why:** OSNet was specifically designed for person re-identification with a lightweight architecture. x0.25 runs on CPU in ~20ms per crop, acceptable for offline processing. The 512-d embedding space allows robust cosine similarity matching with a 0.70 threshold that balances false-positive/negative rate.

**Trade-off:** OSNet requires torchreid which is a large dependency. Alternative would be a colour histogram approach (faster, no dependency) but with far weaker identity discrimination.

---

### 4. SQLite with WAL mode (L7)

**What:** SQLite in WAL (Write-Ahead Logging) mode as the API database.

**Why:** The pipeline runs offline — there is no concurrent write load requiring PostgreSQL. SQLite ships zero-config, has no Docker service dependency, and WAL mode supports concurrent reads alongside writes. The events table is append-only with `INSERT OR IGNORE` idempotency.

**Trade-off:** Cannot scale horizontally. If the API needed to run across multiple processes, a server-based database would be required. Acceptable for a single-store analytics use case.

---

### 5. Group entry pre-scan (L4)

**What:** Before processing any events, a first pass over the zone events file clusters ZONE_ENTER(INSIDE) timestamps within a 4-second window. All members of a cluster get the same `group_id` before any event is emitted.

**Why:** Without the pre-scan, the first person in a group always gets `group_id=None` (because no one else has entered yet when their event fires). The pre-scan solves this: all group members are identified upfront so every event in the group carries the same `group_id`.

**Trade-off:** Requires loading the file twice. Acceptable because the files are small (seconds to load).

---

## Event Schema

All events across L3–L7 use the same schema:

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

Event types in the pipeline:

| Event | Source | Meaning |
|---|---|---|
| `ZONE_ENTER` | L3 | Person centroid entered a zone polygon |
| `ZONE_EXIT` | L3 | Person centroid left a zone polygon |
| `ZONE_DWELL` | L3 | Person present in zone for 30+ seconds |
| `ENTRY` | L4 | Person crossed from OUTSIDE to INSIDE zone |
| `EXIT` | L4 | Person crossed from INSIDE back to OUTSIDE zone |
| `REENTRY` | L5 | Known visitor matched via Re-ID embedding |
| `VISITOR_LINKED` | L5 | Same person seen on a different camera |
| `STAFF_FLAGGED` | L5 | Track classified as store staff |
| `BILLING_QUEUE_JOIN` | L6 | Visitor reached billing zone |
| `BILLING_QUEUE_ABANDON` | L6 | Visitor left billing zone before converting |

---

## AI-Assisted Decisions

This project used Claude (Anthropic) as a development assistant for:

1. **Zone-transition entry logic** — The original line-crossing approach produced false positives (people walking parallel to the door). Claude helped reason through the state machine: track OUTSIDE-* → INSIDE-* zone transitions instead of geometrically crossing a line.

2. **Group detection pre-scan** — The first design left the first group member without a `group_id`. Claude identified the asymmetry and suggested the two-pass approach (pre-scan all entries, cluster by time window, assign group IDs before the main loop runs).

3. **Re-ID shape guard** — When a crop extraction failed (returns None), the original code stored `np.zeros(1)` as a placeholder. When this 1-d vector was later compared against a 512-d OSNet embedding, numpy raised a shape mismatch. Claude identified that the fix was to skip the cache entirely when embedding is None rather than storing a sentinel value.

4. **FastAPI migration** — The initial implementation used Flask. Claude rewrote all blueprints to FastAPI APIRouters, converted the test fixtures from `flask.testing.FlaskClient` to `fastapi.testclient.TestClient`, and handled the request body parsing edge case (invalid JSON returning 400 instead of FastAPI's default 422).

5. **L6 merge architecture** — Claude designed the 7-step merge pipeline (load → map → staff → reentry → POS → seq → build) as separate single-responsibility modules rather than one monolithic merge function.
