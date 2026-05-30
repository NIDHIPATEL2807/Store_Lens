"""
Main pipeline.

Run:  python main.py
  - Reads every .mp4/.avi/.mov from INPUT/
  - Camera type is inferred from the filename (entry/floor/billing keyword)
  - Emits business events frame-by-frame (ENTRY, ZONE_ENTER, ZONE_DWELL,
    BILLING_QUEUE_JOIN, EXIT, REENTRY)
  - Writes one .jsonl per video into OUTPUT/

Zones are defined in config.json using 0–1 normalised coordinates.
"""

from __future__ import annotations

import cv2
import json
import uuid
import hashlib
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

from detect import YOLODetector
from track import ByteTracker, TrackedObject

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_BASE = Path(__file__).parent
CONFIG_PATH = _BASE / "config.json"
INPUT_DIR   = _BASE / "INPUT"
OUTPUT_DIR  = _BASE / "OUTPUT"


# ---------------------------------------------------------------------------
# Zone geometry
# ---------------------------------------------------------------------------
def _point_in_polygon(point: tuple[float, float], polygon: list[list[float]]) -> bool:
    """Ray-casting point-in-polygon test."""
    x, y = point
    n = len(polygon)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        if ((yi > y) != (yj > y)) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def _scale_polygon(polygon: list[list[float]], w: int, h: int) -> list[list[float]]:
    """Convert 0–1 normalised coords → pixel coords for a given frame size."""
    return [[p[0] * w, p[1] * h] for p in polygon]


# ---------------------------------------------------------------------------
# Event factory
# ---------------------------------------------------------------------------
def _make_event(
    store_id: str,
    camera_id: str,
    visitor_id: str,
    event_type: str,
    timestamp: datetime,
    zone_id: Optional[str],
    dwell_ms: int,
    is_staff: bool,
    confidence: float,
    queue_depth: Optional[int],
    sku_zone: Optional[str],
    session_seq: int,
) -> dict:
    return {
        "event_id":   str(uuid.uuid4()),
        "store_id":   store_id,
        "camera_id":  camera_id,
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp":  timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "zone_id":    zone_id,
        "dwell_ms":   dwell_ms,
        "is_staff":   is_staff,
        "confidence": confidence,
        "metadata": {
            "queue_depth": queue_depth,
            "sku_zone":    sku_zone,
            "session_seq": session_seq,
        },
    }


def _visitor_id(camera_id: str, track_id: int) -> str:
    """Stable, short visitor ID derived from camera + ByteTrack ID."""
    return "VIS_" + hashlib.md5(f"{camera_id}_{track_id}".encode()).hexdigest()[:6]


# ---------------------------------------------------------------------------
# Per-track state
# ---------------------------------------------------------------------------
@dataclass
class _TrackState:
    visitor_id: str
    track_id: int
    first_frame: int
    session_seq: int = 0
    is_staff: bool = False
    last_confidence: float = 0.0
    current_zone: Optional[str] = None
    zone_enter_frame: int = 0
    last_dwell_frame: int = 0
    exited: bool = False
    exit_frame: int = 0


# ---------------------------------------------------------------------------
# Per-camera event logic
# ---------------------------------------------------------------------------
class _CameraPipeline:
    def __init__(
        self,
        camera_type: str,
        camera_cfg: dict,
        store_id: str,
        fps: float,
        cfg: dict,
    ):
        self.camera_type = camera_type
        self.camera_id   = camera_cfg["camera_id"]
        self.store_id    = store_id
        self.fps         = fps

        # Thresholds in frames
        self._staff_frames = int(cfg.get("staff_threshold_minutes", 10) * 60 * fps)
        self._dwell_frames = int(cfg.get("dwell_threshold_seconds", 30) * fps)
        self._reentry_frames = int(cfg.get("reentry_window_seconds", 90) * fps)

        self._zones_raw: dict = camera_cfg.get("zones", {})
        self._zones: dict = {}          # populated after first frame size is known

        self._tracks: dict[int, _TrackState] = {}
        self._recently_exited: list[_TrackState] = []
        self.events: list[dict] = []

    # ------------------------------------------------------------------
    def set_frame_size(self, width: int, height: int) -> None:
        self._zones = {}
        for name, defn in self._zones_raw.items():
            if isinstance(defn, list):
                self._zones[name] = {
                    "polygon": _scale_polygon(defn, width, height),
                    "sku_zone": None,
                }
            else:
                self._zones[name] = {
                    "polygon": _scale_polygon(defn["polygon"], width, height),
                    "sku_zone": defn.get("sku_zone"),
                }

    # ------------------------------------------------------------------
    def _ts(self, frame_idx: int, video_start: datetime) -> datetime:
        return video_start + timedelta(seconds=frame_idx / self.fps)

    def _zone_at(self, cx: float, cy: float) -> tuple[Optional[str], Optional[str]]:
        for name, info in self._zones.items():
            if _point_in_polygon((cx, cy), info["polygon"]):
                return name, info["sku_zone"]
        return None, None

    def _emit(self, evt: dict) -> None:
        self.events.append(evt)

    # ------------------------------------------------------------------
    def process_frame(
        self,
        frame_idx: int,
        tracked: list[TrackedObject],
        video_start: datetime,
    ) -> None:
        active_ids = {t.track_id for t in tracked}
        ts = self._ts(frame_idx, video_start)

        # ── Detect disappearing tracks ────────────────────────────────
        for tid, state in list(self._tracks.items()):
            if tid in active_ids or state.exited:
                continue
            state.exited = True
            state.exit_frame = frame_idx
            self._recently_exited.append(state)

            if self.camera_type == "entry":
                state.session_seq += 1
                self._emit(_make_event(
                    self.store_id, self.camera_id, state.visitor_id,
                    "EXIT", ts, None, 0, state.is_staff,
                    state.last_confidence, None, None, state.session_seq,
                ))

        # Expire stale reentry candidates
        self._recently_exited = [
            s for s in self._recently_exited
            if (frame_idx - s.exit_frame) <= self._reentry_frames
        ]

        # ── Process active tracks ─────────────────────────────────────
        for obj in tracked:
            tid = obj.track_id
            cx, cy = obj.center

            # ── New track ─────────────────────────────────────────────
            if tid not in self._tracks:
                reentry_source: Optional[_TrackState] = None

                if self.camera_type == "entry" and self._recently_exited:
                    # Match to the most recent exited visitor (FIFO for single-door stores)
                    reentry_source = self._recently_exited.pop(0)

                if reentry_source:
                    state = _TrackState(
                        visitor_id=reentry_source.visitor_id,
                        track_id=tid,
                        first_frame=frame_idx,
                        is_staff=reentry_source.is_staff,
                        session_seq=0,
                    )
                    self._tracks[tid] = state
                    state.session_seq += 1
                    self._emit(_make_event(
                        self.store_id, self.camera_id, state.visitor_id,
                        "REENTRY", ts, None, 0, state.is_staff,
                        obj.confidence, None, None, state.session_seq,
                    ))
                else:
                    state = _TrackState(
                        visitor_id=_visitor_id(self.camera_id, tid),
                        track_id=tid,
                        first_frame=frame_idx,
                    )
                    self._tracks[tid] = state

                    if self.camera_type == "entry":
                        state.session_seq += 1
                        self._emit(_make_event(
                            self.store_id, self.camera_id, state.visitor_id,
                            "ENTRY", ts, None, 0, state.is_staff,
                            obj.confidence, None, None, state.session_seq,
                        ))

            state = self._tracks[tid]
            state.exited = False
            state.last_confidence = obj.confidence

            # Staff heuristic: continuously visible longer than threshold
            if not state.is_staff and (frame_idx - state.first_frame) >= self._staff_frames:
                state.is_staff = True

            # ── Zone logic (floor + billing cameras) ──────────────────
            if self.camera_type not in ("floor", "billing"):
                continue

            zone_name, sku_zone = self._zone_at(cx, cy)

            if zone_name != state.current_zone:
                state.current_zone = zone_name
                state.zone_enter_frame = frame_idx
                state.last_dwell_frame = frame_idx

                if not zone_name:
                    continue

                if self.camera_type == "floor":
                    state.session_seq += 1
                    self._emit(_make_event(
                        self.store_id, self.camera_id, state.visitor_id,
                        "ZONE_ENTER", ts, zone_name, 0, state.is_staff,
                        obj.confidence, None, sku_zone, state.session_seq,
                    ))

                elif self.camera_type == "billing":
                    queue_depth = sum(
                        1 for t in tracked
                        if self._zone_at(*t.center)[0] == "BILLING"
                    )
                    state.session_seq += 1
                    self._emit(_make_event(
                        self.store_id, self.camera_id, state.visitor_id,
                        "BILLING_QUEUE_JOIN", ts, "BILLING", 0, state.is_staff,
                        obj.confidence, queue_depth, None, state.session_seq,
                    ))

            elif zone_name and self.camera_type == "floor":
                # Periodic dwell check — emit every dwell_threshold seconds of continuous presence
                if (frame_idx - state.last_dwell_frame) >= self._dwell_frames:
                    dwell_ms = int((frame_idx - state.zone_enter_frame) / self.fps * 1000)
                    state.session_seq += 1
                    state.last_dwell_frame = frame_idx
                    self._emit(_make_event(
                        self.store_id, self.camera_id, state.visitor_id,
                        "ZONE_DWELL", ts, zone_name, dwell_ms, state.is_staff,
                        obj.confidence, None, sku_zone, state.session_seq,
                    ))


# ---------------------------------------------------------------------------
# Video processor
# ---------------------------------------------------------------------------
def _camera_type_from_name(stem: str) -> str:
    name = stem.lower()
    if any(k in name for k in ("entry", "door", "entrance")):
        return "entry"
    if any(k in name for k in ("billing", "checkout", "cashier")):
        return "billing"
    return "floor"


def _video_start_time(video_path: Path) -> datetime:
    """Use file mtime as the video's wall-clock start time."""
    mtime = os.path.getmtime(video_path)
    return datetime.fromtimestamp(mtime, tz=timezone.utc)


def process_video(video_path: Path, cfg: dict, detector: YOLODetector) -> list[dict]:
    camera_type = _camera_type_from_name(video_path.stem)
    camera_cfg  = cfg["cameras"][camera_type]

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open: {video_path}")

    fps    = cap.get(cv2.CAP_PROP_FPS) or cfg.get("fps", 15.0)
    width  = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    pipeline = _CameraPipeline(
        camera_type=camera_type,
        camera_cfg=camera_cfg,
        store_id=cfg["store_id"],
        fps=fps,
        cfg=cfg,
    )
    pipeline.set_frame_size(width, height)

    tracker     = ByteTracker(fps=fps)
    video_start = _video_start_time(video_path)
    log_every   = max(1, int(fps * 10))  # log every ~10 s of footage

    logging.info(
        "  camera=%s  size=%dx%d  fps=%.1f  start=%s",
        camera_type, width, height, fps,
        video_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
    )

    frame_idx = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break

        detections = detector.detect(frame)
        tracked    = tracker.update(detections, frame.shape)
        pipeline.process_frame(frame_idx, tracked, video_start)

        if frame_idx % log_every == 0:
            logging.info(
                "    frame %d | tracks=%d | events=%d",
                frame_idx, len(tracked), len(pipeline.events),
            )

        frame_idx += 1

    cap.release()
    logging.info("  done: %d frames → %d events", frame_idx, len(pipeline.events))
    return pipeline.events


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%H:%M:%S",
    )

    with open(CONFIG_PATH) as f:
        cfg = json.load(f)

    OUTPUT_DIR.mkdir(exist_ok=True)
    INPUT_DIR.mkdir(exist_ok=True)

    detector = YOLODetector(
        model_path=cfg.get("model_path", "yolov8n.pt"),
        conf_threshold=cfg.get("confidence_threshold", 0.4),
        device=cfg.get("device", "cpu"),
    )

    video_exts = {".mp4", ".avi", ".mov", ".mkv"}
    videos = [p for p in INPUT_DIR.iterdir() if p.suffix.lower() in video_exts]

    if not videos:
        logging.warning("No videos found in %s", INPUT_DIR)
        return

    for video_path in sorted(videos):
        logging.info("\n=== %s ===", video_path.name)
        try:
            events = process_video(video_path, cfg, detector)
        except Exception:
            logging.exception("Failed to process %s", video_path.name)
            continue

        out_path = OUTPUT_DIR / (video_path.stem + ".jsonl")
        with open(out_path, "w") as f:
            for evt in events:
                f.write(json.dumps(evt) + "\n")
        logging.info("Wrote %d events → %s", len(events), out_path.name)


if __name__ == "__main__":
    main()
