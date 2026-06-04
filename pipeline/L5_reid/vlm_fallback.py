"""
VLM staff-check fallback using Groq API (Llama 4 vision).

Called ONCE per ambiguous track_id (HSV confidence 0.25–0.50).
Result is cached by caller — never called per frame.

Loads GROQ_API_KEY from .env in the pipeline/ parent directory.
"""

import base64
import os
from pathlib import Path
import cv2
import numpy as np

# ── Load .env ─────────────────────────────────────────────────────────────────

def _load_groq_key() -> str:
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.lower().startswith("groq_api_key"):
                return line.split("=", 1)[1].strip()
    return os.environ.get("GROQ_API_KEY", "")


_GROQ_KEY = _load_groq_key()
_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"

_PROMPT = (
    "You are a retail store CCTV analyst. Look at this person in the image. "
    "Are they wearing a retail staff uniform — such as an apron, branded vest, "
    "matching store outfit, or any clothing that clearly identifies them as an employee? "
    "Reply with exactly one word: yes / no / unsure"
)


def groq_staff_check(crop_bgr: np.ndarray) -> tuple[bool, float]:
    """
    Returns (is_staff, confidence).
    Falls back to (False, 0.5) if API is unavailable.
    """
    if not _GROQ_KEY:
        print("[vlm_fallback] No Groq API key — skipping VLM check")
        return False, 0.5

    try:
        from groq import Groq
    except ImportError:
        print("[vlm_fallback] groq package not installed — pip install groq")
        return False, 0.5

    try:
        _, buf = cv2.imencode(".jpg", crop_bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
        b64 = base64.b64encode(buf.tobytes()).decode()

        client = Groq(api_key=_GROQ_KEY)
        resp = client.chat.completions.create(
            model=_MODEL,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    {"type": "text", "text": _PROMPT},
                ],
            }],
            max_tokens=5,
            temperature=0.0,
        )
        answer = resp.choices[0].message.content.strip().lower()

        if answer.startswith("yes"):
            return True, 0.90
        if answer.startswith("no"):
            return False, 0.90
        return False, 0.50  # unsure → treat as customer

    except Exception as e:
        print(f"[vlm_fallback] Groq API error: {e}")
        return False, 0.50
