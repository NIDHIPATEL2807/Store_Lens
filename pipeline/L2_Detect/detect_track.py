"""
L2 Detect & Track — Core processor

One DetectTracker instance per camera per clip.
Reads the video frame-by-frame, detects persons with YOLOv8,
tracks them with ByteTrack (via supervision), and emits one
JSONL record per processed frame.
"""

import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from ultralytics import YOLO
import supervision as sv

from timestamp_utils import parse_clip_start, frame_timestamp

logger = logging.getLogger(__name__)

# ── per-camera-type detection thresholds ─────────────────────────────────────
# Entry cameras are stricter to avoid glass-door reflection false positives.
# Zone cameras are more lenient because top-down angle lowers raw confidence.
CONF_BY_TYPE: dict[str, float] = {
    "zone":    0.30,
    "billing": 0.35,
    "entry":   0.40,
}
DEFAULT_CONF = 0.35

MIN_BBOX_W = 30        # pixels — anything narrower is not a real person
MIN_BBOX_H = 40        # pixels — anything shorter is noise / partial limb
EDGE_MARGIN_FRAC = 0.025  # 2.5% of frame dimension — scales with any resolution
OCCLUSION_AREA = 200_000  # px² — blob this large likely contains 2+ merged persons

LONG_DURATION_SECONDS = 10 * 60   # 10 min → possible staff signal for L5
GROUP_WINDOW_SECONDS  = 2.0       # two tracks appearing within 2s → same group
TRACK_BUFFER_SECONDS  = 2.0       # keep track alive N seconds after last detection
CONSEC_FAIL_LIMIT     = 50        # consecutive failed cap.read() before abort


@dataclass
class _TrackState:
    first_seen_frame: int
    first_seen_seconds: float
    group_id: Optional[str] = None


class DetectTracker:
    """Wraps YOLO + ByteTrack for a single camera configuration."""

    def __init__(
        self,
        camera_config: dict,
        store_id: str,
        model_path: str = "yolov8n.pt",
        frame_skip: int = 3,
    ):
        self.cam       = camera_config
        self.cam_id    = camera_config["camera_id"]
        self.cam_type  = camera_config.get("camera_type", "zone")
        self.store_id  = store_id
        self.frame_skip = frame_skip

        self.conf_thresh = CONF_BY_TYPE.get(self.cam_type, DEFAULT_CONF)

        self.model = YOLO(model_path)
        self.model.fuse()

        # Zones with type "outside" are treated as reflection / exclusion zones.
        # Annotate them in L1 for entry cameras that have glass doors.
        self._reflection_polys: list[np.ndarray] = [
            np.array(z["polygon"], dtype=np.int32)
            for z in camera_config.get("zones", [])
            if z.get("zone_type") == "outside"
        ]

        # track bookkeeping
        self._track_states: dict[int, _TrackState] = {}
        self._group_counter = 0
        # sliding window of (seconds_from_start, track_id) for group detection
        self._new_track_window: deque[tuple[float, int]] = deque()

    # ── public API ────────────────────────────────────────────────────────────

    def process_video(
        self,
        video_path: str,
        output_path: str,
        clip_start_utc: Optional[str] = None,
    ) -> dict:
        """
        Process one video clip end-to-end.
        Writes JSONL to output_path (one record per processed frame).
        Returns a summary stats dict.
        """
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open video: {video_path}")

        fps    = cap.get(cv2.CAP_PROP_FPS) or 15.0
        vid_w  = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        vid_h  = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

        self._validate_resolution(vid_w, vid_h)

        clip_start = parse_clip_start(video_path, clip_start_utc)
        if clip_start is None:
            logger.warning(
                "%s: no clip_start_utc found in filename or args — "
                "timestamps will be wrong. Pass --clip_start to fix.",
                self.cam_id,
            )
            clip_start = datetime(1970, 1, 1, tzinfo=timezone.utc)

        tracker = self._make_tracker(fps)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)

        frames_processed = 0
        low_conf_total   = 0
        consec_fail      = 0
        frame_num        = 0

        with open(output_path, "w", encoding="utf-8") as out_f:
            while True:
                ret, frame = cap.read()

                if not ret:
                    consec_fail += 1
                    if consec_fail > CONSEC_FAIL_LIMIT:
                        logger.error(
                            "%s: %d consecutive failed reads — stopping.",
                            self.cam_id, consec_fail,
                        )
                        break
                    logger.debug("%s: dropped raw frame %d", self.cam_id, frame_num)
                    if frame_num % self.frame_skip == 0:
                        ts = frame_timestamp(clip_start, frame_num, fps)
                        out_f.write(json.dumps(self._dropped_record(frame_num, ts)) + "\n")
                    frame_num += 1
                    continue

                consec_fail = 0

                # process every Nth frame only
                if frame_num % self.frame_skip != 0:
                    frame_num += 1
                    continue

                t0 = time.perf_counter()
                ts = frame_timestamp(clip_start, frame_num, fps)
                seconds_in = frame_num / fps

                record, n_tracks, n_low = self._process_frame(
                    frame, frame_num, ts, seconds_in, tracker, vid_w, vid_h,
                )
                record["frame_meta"]["processing_ms"] = int(
                    (time.perf_counter() - t0) * 1000
                )

                out_f.write(json.dumps(record) + "\n")
                frames_processed += 1
                low_conf_total   += n_low
                frame_num        += 1

        cap.release()

        return {
            "camera_id":        self.cam_id,
            "video":            video_path,
            "frames_processed": frames_processed,
            "low_conf_dropped": low_conf_total,
            "output":           output_path,
        }

    # ── per-frame logic ───────────────────────────────────────────────────────

    def _process_frame(
        self,
        frame: np.ndarray,
        frame_num: int,
        timestamp: str,
        seconds_in: float,
        tracker: sv.ByteTrack,
        vid_w: int,
        vid_h: int,
    ) -> tuple[dict, int, int]:
        """Returns (record, n_accepted_tracks, n_low_conf_dropped)."""

        # ── detect (persons only) ─────────────────────────────────────
        results = self.model.predict(
            frame,
            classes=[0],
            conf=self.conf_thresh,
            verbose=False,
        )
        dets = sv.Detections.from_ultralytics(results[0])

        low_conf_dropped = 0

        if len(dets) > 0:
            # ultralytics already filters by conf, but log anything below
            # the hard floor (0.35) that slipped through lower-conf camera types
            if dets.confidence is not None:
                low_mask = dets.confidence < 0.35
                low_conf_dropped = int(low_mask.sum())

            # size + edge filter — hands/reflections/products and partial detections clipped by frame boundary
            bx = dets.xyxy
            w_arr = bx[:, 2] - bx[:, 0]
            h_arr = bx[:, 3] - bx[:, 1]
            size_ok = (w_arr >= MIN_BBOX_W) & (h_arr >= MIN_BBOX_H)
            xm = int(vid_w * EDGE_MARGIN_FRAC)
            ym = int(vid_h * EDGE_MARGIN_FRAC)
            edge_ok = (
                (bx[:, 0] >= xm) &
                (bx[:, 1] >= ym) &
                (bx[:, 2] <= vid_w - xm) &
                (bx[:, 3] <= vid_h - ym)
            )
            dets = dets[size_ok & edge_ok]

        # ── track ─────────────────────────────────────────────────────
        if len(dets) > 0:
            dets = tracker.update_with_detections(dets)
        else:
            tracker.update_with_detections(sv.Detections.empty())

        # ── build track records ───────────────────────────────────────
        tracks_out: list[dict] = []

        if len(dets) > 0 and dets.tracker_id is not None:
            # group entry detection for entry cameras
            if self.cam_type == "entry":
                self._assign_groups(dets, seconds_in)

            for i in range(len(dets)):
                tid  = int(dets.tracker_id[i])
                bbox = [int(v) for v in dets.xyxy[i]]
                conf = float(dets.confidence[i]) if dets.confidence is not None else 0.0
                cx   = int((bbox[0] + bbox[2]) / 2)
                cy   = int((bbox[1] + bbox[3]) / 2)

                if self._in_reflection_zone(cx, cy):
                    continue

                state     = self._ensure_state(tid, frame_num, seconds_in)
                duration  = seconds_in - state.first_seen_seconds
                long_flag = duration >= LONG_DURATION_SECONDS
                bbox_area = int((bbox[2] - bbox[0]) * (bbox[3] - bbox[1]))

                track_rec: dict = {
                    "track_id":           tid,
                    "bbox":               bbox,
                    "centroid":           [cx, cy],
                    "confidence":         round(conf, 3),
                    "bbox_area":          bbox_area,
                    "occluded":           bbox_area > OCCLUSION_AREA,
                    "long_duration_flag": long_flag,
                }
                if state.group_id:
                    track_rec["group_id"] = state.group_id

                tracks_out.append(track_rec)

        record = {
            "frame":      frame_num,
            "timestamp":  timestamp,
            "camera_id":  self.cam_id,
            "store_id":   self.store_id,
            "tracks":     tracks_out,
            "frame_meta": {
                "total_detections": len(tracks_out),
                "dropped":          False,
                "processing_ms":    0,
            },
        }
        return record, len(tracks_out), low_conf_dropped

    # ── helpers ───────────────────────────────────────────────────────────────

    def _make_tracker(self, fps: float) -> sv.ByteTrack:
        effective_fps = max(1.0, fps / self.frame_skip)
        lost_buffer   = max(1, int(TRACK_BUFFER_SECONDS * effective_fps))
        return sv.ByteTrack(
            track_activation_threshold=self.conf_thresh,
            lost_track_buffer=lost_buffer,
            minimum_matching_threshold=0.8,
            frame_rate=int(effective_fps),
        )

    def _validate_resolution(self, vid_w: int, vid_h: int) -> None:
        exp_w = self.cam.get("frame_width")
        exp_h = self.cam.get("frame_height")
        if exp_w and exp_h and (vid_w != exp_w or vid_h != exp_h):
            logger.warning(
                "%s: resolution mismatch — video is %dx%d but store_layout.json "
                "says %dx%d. Using actual video resolution.",
                self.cam_id, vid_w, vid_h, exp_w, exp_h,
            )

    def _in_reflection_zone(self, cx: int, cy: int) -> bool:
        """Return True if centroid falls inside any annotated reflection polygon."""
        for poly in self._reflection_polys:
            if cv2.pointPolygonTest(poly, (float(cx), float(cy)), False) >= 0:
                return True
        return False

    def _ensure_state(self, tid: int, frame_num: int, seconds_in: float) -> _TrackState:
        if tid not in self._track_states:
            self._track_states[tid] = _TrackState(
                first_seen_frame=frame_num,
                first_seen_seconds=seconds_in,
            )
        return self._track_states[tid]

    def _assign_groups(self, dets: sv.Detections, seconds_in: float) -> None:
        """
        Entry camera only: if 2+ brand-new track IDs appear within
        GROUP_WINDOW_SECONDS of each other, assign them the same group_id.
        """
        if dets.tracker_id is None:
            return

        current_ids  = set(dets.tracker_id.tolist())
        new_ids      = current_ids - set(self._track_states.keys())

        # evict expired entries from the sliding window
        cutoff = seconds_in - GROUP_WINDOW_SECONDS
        while self._new_track_window and self._new_track_window[0][0] < cutoff:
            self._new_track_window.popleft()

        for tid in new_ids:
            nearby_ids = [t for _, t in self._new_track_window]
            if nearby_ids:
                # reuse an existing group if one of the nearby tracks has one
                group_id = next(
                    (self._track_states[t].group_id for t in nearby_ids
                     if t in self._track_states and self._track_states[t].group_id),
                    None,
                )
                if not group_id:
                    self._group_counter += 1
                    group_id = f"GRP_{self._group_counter:04d}"
                    for t in nearby_ids:
                        if t in self._track_states:
                            self._track_states[t].group_id = group_id

                state = self._ensure_state(tid, 0, seconds_in)
                state.group_id = group_id

            self._new_track_window.append((seconds_in, tid))

    def _dropped_record(self, frame_num: int, timestamp: str) -> dict:
        return {
            "frame":      frame_num,
            "timestamp":  timestamp,
            "camera_id":  self.cam_id,
            "store_id":   self.store_id,
            "tracks":     [],
            "frame_meta": {"total_detections": 0, "dropped": True, "processing_ms": 0},
        }
