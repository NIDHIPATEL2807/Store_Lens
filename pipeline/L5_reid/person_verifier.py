"""
YOLO-based person crop verifier.

L2 tracks come from YOLO+ByteTrack, but tracks can drift onto objects
(bags, chairs, mannequins) that were briefly misclassified as persons.
Before running staff detection or Re-ID on a crop, confirm it actually
contains a person.

Loads YOLOv8n once at module import — reused for all crops.
"""

import numpy as np

_model = None
_PERSON_CLASS = 0
_CONF_THRESHOLD = 0.45  # lower than L2 since crop is already tight


def _get_model():
    global _model
    if _model is None:
        try:
            from ultralytics import YOLO
            _model = YOLO("yolov8n.pt")
            print("[person_verifier] YOLOv8n loaded for crop verification")
        except Exception as e:
            print(f"[person_verifier] YOLO unavailable ({e}) — skipping crop verification")
            _model = False  # sentinel: don't retry
    return _model if _model is not False else None


def is_person(crop_bgr: np.ndarray, conf_threshold: float = _CONF_THRESHOLD) -> bool:
    """
    Returns True if YOLOv8 detects a person in the crop with sufficient confidence.
    Returns True (pass-through) if YOLO is unavailable — don't block processing.
    """
    if crop_bgr is None or crop_bgr.size == 0:
        return False

    h, w = crop_bgr.shape[:2]
    # Crops smaller than 30×60 are too blurry to classify reliably
    if h < 30 or w < 15:
        return False

    model = _get_model()
    if model is None:
        return True  # YOLO not available — assume person and let downstream decide

    try:
        results = model(crop_bgr, verbose=False, imgsz=128)
        for r in results:
            for box in r.boxes:
                if int(box.cls[0]) == _PERSON_CLASS and float(box.conf[0]) >= conf_threshold:
                    return True
        return False
    except Exception as e:
        print(f"[person_verifier] inference error: {e}")
        return True  # fail open
