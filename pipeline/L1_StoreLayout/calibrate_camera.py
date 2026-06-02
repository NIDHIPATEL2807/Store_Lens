import base64
import json
from pathlib import Path
from openai import OpenAI
from utils import call_with_retry

_PROMPT = """You are given two images in order:
  Image 1 — a CCTV camera frame from a retail store
  Image 2 — the store floor plan

Task:
1. Identify which named zones from the floor plan are visible in the camera frame.
2. For each visible zone give an approximate pixel bounding box [x1, y1, x2, y2] inside the camera frame.
3. If this is an entry/door camera, also return the best entry threshold line [x1,y1,x2,y2]
   and direction (bottom_to_top | top_to_bottom | left_to_right | right_to_left).

Known floor plan zones: {zone_names}

Return ONLY valid JSON with no markdown fences and no explanation:
{{
  "visible_zones": [
    {{
      "zone_id": "ZONE_ID_MATCHING_FLOOR_PLAN",
      "zone_name": "...",
      "zone_type": "shelf|billing|entry|boh|floor",
      "is_revenue_zone": true,
      "pixel_bbox": [x1, y1, x2, y2]
    }}
  ],
  "entry_line": {{"x1": 0, "y1": 0, "x2": 0, "y2": 0}},
  "entry_direction": "bottom_to_top"
}}
Note: entry_line and entry_direction are only required for entry-type cameras."""


def _mime(path: str) -> str:
    return "image/png" if Path(path).suffix.lower() == ".png" else "image/jpeg"


def _b64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def calibrate_camera(
    frame_path: str,
    layout_png_path: str,
    layout_zones: dict,
    camera_type: str,
    frame_width: int,
    frame_height: int,
    api_key: str,
    model_name: str = "gemini-2.0-flash",
) -> dict:
    """Match a camera frame to floor-plan zones via OpenRouter VLM."""
    client = OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")

    zone_names = [z["zone_name"] for z in layout_zones.get("zones", [])]
    prompt = _PROMPT.format(zone_names=zone_names)

    response = call_with_retry(
        client.chat.completions.create,
        model=model_name,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:{_mime(frame_path)};base64,{_b64(frame_path)}"}},
                {"type": "image_url", "image_url": {"url": f"data:{_mime(layout_png_path)};base64,{_b64(layout_png_path)}"}},
            ],
        }],
    )

    result = _extract_json(response.choices[0].message.content)

    # Guarantee entry_line exists for entry cameras even if VLM omitted it
    if camera_type == "entry":
        el = result.get("entry_line") or {}
        if not any(el.get(k) for k in ("x1", "x2", "y1", "y2")):
            cx = frame_width // 2
            result["entry_line"] = {"x1": cx, "y1": 0, "x2": cx, "y2": frame_height}
            result.setdefault("entry_direction", "bottom_to_top")

    visible = result.get("visible_zones", [])
    print(f"  [OK]   Sees {len(visible)} zone(s): {[z['zone_id'] for z in visible]}")
    return result


def _extract_json(text: str) -> dict:
    text = text.strip()
    if "```" in text:
        for block in text.split("```"):
            block = block.strip()
            if block.startswith("json"):
                block = block[4:].strip()
            try:
                return json.loads(block)
            except json.JSONDecodeError:
                continue
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"visible_zones": []}
