# Technical Choices

Three key decisions that shaped this pipeline, with the alternatives considered and the reasons for the choices made.

---

## Choice 1 — Zone-based entry/exit detection instead of line crossing

### What was decided
Entry and exit events are detected by watching zone transitions: a person enters the store when their track transitions from an `OUTSIDE-*` annotated polygon to an `INSIDE-*` annotated polygon. The L1 annotation step requires drawing two zones per entry camera — one outside the door, one inside.

### Alternatives considered

| Approach | How it works | Why rejected |
|---|---|---|
| Virtual line crossing | Draw a line across the doorway; fire ENTRY/EXIT when a centroid crosses it | Requires the camera to be perpendicular to the door. Parallel motion (person walking along the front of the store) fires false positives. Sensitive to centroid jitter at the line. |
| Optical flow at door region | Detect net motion direction in a door bounding box | Complex to calibrate, fails with multiple simultaneous crossings, struggles with glass-door reflections |
| Zone-based (chosen) | Two annotated polygons; state machine: OUTSIDE→INSIDE = ENTRY, INSIDE→OUTSIDE = EXIT | Spatially tolerant — the polygons are large, transient positions don't trigger. Works regardless of camera angle or door type. |

### Outcome
Zone-based detection works cleanly across Store 2's two entry cameras at different angles. It naturally handles the "born inside" case (track first appears inside the store) and the "loitering at door" case (person steps into INSIDE zone but immediately goes back — detected as ENTRY + EXIT with short dwell).

---

## Choice 2 — OSNet x0.25 for person re-identification

### What was decided
Cross-camera visitor matching uses OSNet x0.25, a lightweight person re-identification model producing 512-dimensional appearance embeddings. Visitors are matched using cosine similarity with a 0.70 threshold.

### Alternatives considered

| Approach | Accuracy | Speed | Why rejected / chosen |
|---|---|---|---|
| Colour histogram matching | Low | Very fast | Fails when two people wear similar colours; can't distinguish same-colour-different-person |
| ResNet-50 Re-ID backbone | High | Slow (GPU needed) | Too heavy for CPU-only inference in an offline pipeline |
| OSNet x0.25 (chosen) | Good | ~20ms/crop on CPU | Designed specifically for Re-ID; x0.25 is the smallest variant; runs on CPU at acceptable speed |
| DeepSORT appearance model | Medium | Medium | Requires per-dataset fine-tuning; less plug-and-play than OSNet |

### Outcome
OSNet correctly matches the same visitor across zone and entry cameras in Store 1. The 512-d embedding space provides enough discriminative power for short clips with a small population. Cross-camera VISITOR_LINKED events appear in the output.

---

## Choice 3 — SQLite with WAL mode for the analytics API

### What was decided
The L7 API stores events in a single SQLite file (`data/store.db`) using WAL (Write-Ahead Logging) journal mode. All reads are served from the same file; writes use `INSERT OR IGNORE` for idempotency.

### Alternatives considered

| Database | Setup complexity | Concurrency | Why rejected / chosen |
|---|---|---|---|
| PostgreSQL | High (Docker service, credentials, migrations) | Excellent | Overkill for offline single-process analytics; adds Docker dependency complexity |
| DuckDB | Medium | Read-optimised | OLAP-focused; less suited to small write-heavy ingest batches |
| SQLite default mode | Zero | Poor (write lock) | Write lock causes 503 under concurrent ingest + read; WAL fixes this |
| SQLite + WAL (chosen) | Zero | Good for this use case | WAL allows concurrent readers + one writer. No service, no Docker, zero-config. |

### Why WAL specifically
SQLite in default (DELETE) journal mode uses a file-level write lock. Any read request during a write returns `SQLITE_BUSY`. WAL mode eliminates this: readers and the writer operate concurrently without blocking each other. For a batch-ingest + read API, this is the critical difference between 503 errors and smooth operation.

### Outcome
The API handles `/events/ingest` (write) and `/stores/{id}/metrics` (read) simultaneously without contention. Tested with 197-event batches. If the use case grows to multiple API processes, the natural upgrade path is PostgreSQL with the same SQL queries.
