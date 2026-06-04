# Technical Choices

Five key decisions that shaped this pipeline, with the alternatives considered and the reasons for the choices made.

---

## Choice 1 — Build a self-annotation tool instead of using external services

### What was decided
L1 ships its own interactive annotation tool. A user runs `python run_annotation.py`, an OpenCV window opens showing the actual camera frame, and they click to draw zone polygons. The tool saves `store_layout.json` — the spatial map that drives every downstream layer.

### Why this is the most important architectural decision
Every other component in the pipeline is parameterized by `store_layout.json`. This means the entire system is camera-agnostic by design. Adding a new store requires annotating zones — zero code changes anywhere.

The alternative (hardcoding pixel coordinates) would have produced a pipeline that only works for these two specific stores at these specific camera positions.

### Alternatives considered

| Approach | Why rejected |
|---|---|
| Hardcode zone coordinates per store | Only works for these 2 stores; breaks if camera moves |
| Use CVAT / LabelStudio / Roboflow | External dependency, requires export/import pipeline, overkill for polygon-on-frame annotation |
| Auto-detect zones using segmentation models | Unreliable without store-specific training data; can't distinguish "billing counter" from "shelf" semantically |
| Self-annotation tool (chosen) | Runs locally, draws on actual camera frames, saves directly to the schema every layer reads |

### What makes this annotation tool specific to the problem
- Annotates on **real camera frames** (not floor plans) so polygons are pixel-accurate for the camera's perspective and lens distortion
- Entry cameras get a **two-phase workflow**: zone polygons (OUTSIDE/INSIDE) + entry threshold line + crossing direction
- Zone type is captured at annotation time (`shelf`, `entry`, `billing`, `floor`, `boh`) and propagates to every downstream event as `sku_zone` and `is_revenue_zone`
- Auto-backup before every save — annotation work is never lost

---

## Choice 2 — Zone-based entry detection instead of line crossing

### What was decided
Entry and exit events fire on zone transitions: a person enters when their track moves from an `OUTSIDE-*` polygon to an `INSIDE-*` polygon. No virtual line, no direction vector.

### Alternatives considered

| Approach | Why rejected |
|---|---|
| Virtual line crossing | False positives for parallel motion (person walking along storefront). Sensitive to centroid jitter. Requires perpendicular camera. |
| Optical flow in door region | Fails with multiple simultaneous crossings. Complex to calibrate per camera. |
| Zone-based (chosen) | Spatially tolerant. Works at any camera angle. Handles door loitering naturally (enter + immediate exit = short dwell). |

### Group detection inside L4
A pre-scan pass clusters all ZONE_ENTER(INSIDE) events within a 4-second window before any event is emitted. This ensures every group member gets the same `group_id` — including the first person to enter, who would otherwise have no group ID because no one else had entered yet at the time their event fires.

### Outcome
Works cleanly across Store 2's two entry cameras at different angles and heights. Correctly handles the "born inside" case (track first appears inside store → immediate ENTRY). Produces 32 valid entry events for Store 2.

---

## Choice 3 — OSNet x0.25 for person re-identification

### What was decided
Cross-camera visitor matching uses OSNet x0.25 — 512-dimensional appearance embeddings with cosine similarity at 0.70 threshold.

### Alternatives considered

| Approach | Accuracy | Speed | Why rejected / chosen |
|---|---|---|---|
| Colour histogram only | Low | Very fast | Can't distinguish two people in similar clothes |
| ResNet-50 Re-ID | High | Slow (needs GPU) | Too heavy for CPU-only offline pipeline |
| OSNet x0.25 (chosen) | Good | ~20ms/crop on CPU | Designed for Re-ID, smallest variant, CPU-friendly |
| DeepSORT appearance | Medium | Medium | Needs per-dataset fine-tuning |

### Critical implementation detail
When a crop extraction fails (person partially out of frame), the embedding is `None`. Storing `np.zeros(1)` as a placeholder and later comparing it to a 512-d vector crashes with a numpy shape mismatch. The fix: skip the embedding cache entirely when the crop is None. This was a subtle bug that would have crashed Re-ID silently for edge-case tracks.

---

## Choice 4 — SQLite with WAL mode for the analytics API

### What was decided
Single file `data/store.db` in WAL journal mode. `INSERT OR IGNORE` with `cursor.rowcount` check for idempotent ingest.

### Alternatives considered

| Database | Setup | Why rejected / chosen |
|---|---|---|
| PostgreSQL | High (Docker, credentials, migrations) | Overkill for offline single-process analytics |
| DuckDB | Medium | OLAP-focused; less suited to write-heavy ingest |
| SQLite default mode | Zero | Write lock causes 503 under concurrent ingest + read |
| SQLite + WAL (chosen) | Zero | WAL allows concurrent readers + one writer, zero config |

### The rowcount detail
`INSERT OR IGNORE` silently skips duplicate `event_id` values. Without checking `cursor.rowcount`, the code counted every attempted insert as `accepted=1` — even when the row was ignored. The correct pattern:
```python
cur = db.execute(INSERT_SQL, params)
if cur.rowcount > 0:
    accepted += 1
```
This correctly returns `accepted=0` for already-ingested events, making ingest truly idempotent from the client's perspective.

---

## Choice 5 — FastAPI over Flask for the REST API

### What was decided
L7 uses FastAPI + uvicorn instead of Flask.

### Why
FastAPI provides:
- **Automatic Swagger UI** at `/docs` — the scoring harness and evaluators can test every endpoint interactively without writing curl commands
- **Pydantic v2 request validation** built-in — invalid request bodies return structured 422 errors automatically
- **ASGI + async-ready** — better throughput for concurrent requests
- **OpenAPI schema** auto-generated from type annotations — no separate documentation needed

Flask would have required manually writing API documentation and had no built-in validation.

### Migration note
The test fixtures required one non-obvious fix: Flask's `test_client()` creates connections in the same thread. FastAPI's `TestClient` (via Starlette) runs the app in a worker thread. Shared in-memory SQLite connections must be created with `check_same_thread=False`, otherwise every request returns 503 with "SQLite objects created in a thread can only be used in that same thread."
