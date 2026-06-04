"""
Appearance embedding extractor.

Primary:  OSNet x0.25 (torchreid) — 512-d vector, ~30ms/crop on CPU
Fallback: colour + texture histogram if torchreid is not installed.

Both paths return a normalised numpy float32 vector so the rest of the
pipeline never needs to know which backend is running.
"""

import numpy as np
import cv2

_OSNET_AVAILABLE = False
_model = None

try:
    import torch
    import torchreid

    def _load_model():
        global _model, _OSNET_AVAILABLE
        m = torchreid.models.build_model(
            name="osnet_x0_25",
            num_classes=1000,
            pretrained=True,
        )
        m.eval()
        _model = m
        _OSNET_AVAILABLE = True
        print("[embedder] OSNet x0.25 loaded — 512-d embeddings active")

    _load_model()

except Exception as e:
    print(f"[embedder] torchreid not available ({e}) — using histogram fallback")


# ── OSNet path ────────────────────────────────────────────────────────────────

_IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_IMAGENET_STD  = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def _osnet_embed(crop_bgr: np.ndarray) -> np.ndarray:
    import torch
    rgb = cv2.cvtColor(cv2.resize(crop_bgr, (128, 256)), cv2.COLOR_BGR2RGB)
    arr = rgb.astype(np.float32) / 255.0
    arr = (arr - _IMAGENET_MEAN) / _IMAGENET_STD
    t = torch.from_numpy(arr).permute(2, 0, 1).unsqueeze(0)
    with torch.no_grad():
        feat = _model(t)
    v = feat.squeeze().numpy()
    return v / (np.linalg.norm(v) + 1e-8)


# ── Histogram fallback ────────────────────────────────────────────────────────

def _hist_embed(crop_bgr: np.ndarray) -> np.ndarray:
    """128-d HSV colour histogram — fast but less discriminative than OSNet."""
    resized = cv2.resize(crop_bgr, (64, 128))
    hsv = cv2.cvtColor(resized, cv2.COLOR_BGR2HSV)
    h_hist = cv2.calcHist([hsv], [0], None, [64], [0, 180]).flatten()
    s_hist = cv2.calcHist([hsv], [1], None, [32], [0, 256]).flatten()
    v_hist = cv2.calcHist([hsv], [2], None, [32], [0, 256]).flatten()
    vec = np.concatenate([h_hist, s_hist, v_hist]).astype(np.float32)
    return vec / (np.linalg.norm(vec) + 1e-8)


# ── Public API ────────────────────────────────────────────────────────────────

def extract_embedding(crop_bgr: np.ndarray) -> np.ndarray | None:
    """
    Extract an appearance embedding from a BGR crop.
    Returns None if the crop is too small to be reliable.
    """
    if crop_bgr is None or crop_bgr.size == 0:
        return None
    h, w = crop_bgr.shape[:2]
    if h < 20 or w < 10:
        return None

    try:
        if _OSNET_AVAILABLE:
            return _osnet_embed(crop_bgr)
        return _hist_embed(crop_bgr)
    except Exception as e:
        print(f"[embedder] embed error: {e}")
        return None


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-8))


def embedding_dim() -> int:
    return 512 if _OSNET_AVAILABLE else 128
