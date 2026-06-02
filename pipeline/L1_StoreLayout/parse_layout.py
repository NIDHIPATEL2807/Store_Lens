import base64
import json
from openai import OpenAI
from utils import call_with_retry

_PROMPT = """This is a retail store floor plan image.
Identify every distinct named zone visible (shelves, cash counter, entry, back-of-house, etc.).

For each zone return:
  zone_id          - uppercase snake_case identifier  e.g. LEFT_SHELF, BILLING, ENTRY_ZONE, BOH
  zone_name        - human-readable label as it appears on the floor plan
  zone_type        - one of: shelf | billing | entry | boh | floor
  is_revenue_zone  - true for shelf/billing, false for entry/boh/floor
  normalized_bbox  - [x1, y1, x2, y2] as fractions of image width and height (0.0 to 1.0)

Return ONLY valid JSON with no markdown fences and no explanation:
{
  "zones": [
    {
      "zone_id": "...",
      "zone_name": "...",
      "zone_type": "...",
      "is_revenue_zone": true,
      "normalized_bbox": [0.0, 0.0, 1.0, 1.0]
    }
  ]
}"""


def parse_layout(
    layout_png_path: str,
    api_key: str,
    model_name: str = "gemini-2.0-flash",
) -> dict:
    """Send floor plan PNG to OpenRouter VLM and extract zone definitions."""
    client = OpenAI(api_key=api_key, base_url="https://api.groq.com/openai/v1")


    with open(layout_png_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()

    response = call_with_retry(
        client.chat.completions.create,
        model=model_name,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": _PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
            ],
        }],
    )

    text = response.choices[0].message.content
    result = _extract_json(text)
    print(f"  [OK]   Parsed {len(result.get('zones', []))} zones from floor plan")
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
    return json.loads(text)
