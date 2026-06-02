"""
ByteTrack wrapper.
Takes per-frame detections, returns the same detections annotated with stable track IDs
that persist across frames (track #7 in frame 100 = track #7 in frame 200).
"""

from __future__ import annotations

import numpy as np
from dataclasses import dataclass

import supervision as sv

from detect import Detection


@dataclass
class TrackedObject:
    track_id: int
    x1: float
    y1: float
    x2: float
    y2: float
    confidence: float

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2)


class ByteTracker:
    def __init__(self, fps: float = 15.0):
        self._tracker = sv.ByteTrack(
            track_activation_threshold=0.25,
            lost_track_buffer=int(fps * 2),   # 2-second buffer before dropping a track
            minimum_matching_threshold=0.8,
            frame_rate=int(fps),
        )

    def update(self, detections: list[Detection], frame_shape: tuple) -> list[TrackedObject]:
        """Feed one frame's detections; get back objects with stable track IDs."""
        if detections:
            xyxy = np.array([d.xyxy for d in detections], dtype=float)
            confidence = np.array([d.confidence for d in detections], dtype=float)
            class_id = np.zeros(len(detections), dtype=int)
            sv_dets = sv.Detections(xyxy=xyxy, confidence=confidence, class_id=class_id)
        else:
            sv_dets = sv.Detections.empty()

        tracked = self._tracker.update_with_detections(sv_dets)

        results: list[TrackedObject] = []
        if tracked.tracker_id is None:
            return results

        for i in range(len(tracked)):
            tid = tracked.tracker_id[i]
            if tid is None:
                continue
            x1, y1, x2, y2 = tracked.xyxy[i]
            conf = float(tracked.confidence[i]) if tracked.confidence is not None else 0.0
            results.append(TrackedObject(
                track_id=int(tid),
                x1=float(x1), y1=float(y1),
                x2=float(x2), y2=float(y2),
                confidence=conf,
            ))
        return results
