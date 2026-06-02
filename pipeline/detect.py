"""
YOLO-based person detector.
Returns raw bounding boxes + confidence scores per frame — no tracking, no rounding.
"""

from __future__ import annotations

import numpy as np
from dataclasses import dataclass
from ultralytics import YOLO


@dataclass
class Detection:
    x1: float
    y1: float
    x2: float
    y2: float
    confidence: float  # raw YOLO score, never rounded

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2)

    @property
    def xyxy(self) -> list[float]:
        return [self.x1, self.y1, self.x2, self.y2]


class YOLODetector:
    _PERSON_CLASS = 0

    def __init__(
        self,
        model_path: str = "yolov8n.pt",
        conf_threshold: float = 0.4,
        device: str = "cpu",
    ):
        self.model = YOLO(model_path)
        self.conf_threshold = conf_threshold
        self.device = device

    def detect(self, frame: np.ndarray) -> list[Detection]:
        """Run inference on one frame. Returns all person detections above threshold."""
        results = self.model(
            frame,
            conf=self.conf_threshold,
            classes=[self._PERSON_CLASS],
            device=self.device,
            verbose=False,
        )
        detections: list[Detection] = []
        for r in results:
            for box in r.boxes:
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                conf = float(box.conf[0])
                detections.append(Detection(x1=x1, y1=y1, x2=x2, y2=y2, confidence=conf))
        return detections
