"""
Loads L3 / L4 / L5 JSONL files and returns one list sorted by timestamp.
Tags each event with _source so downstream can tell where it came from.
"""

import json
from pathlib import Path


def _load_jsonl(path: Path, source_tag: str) -> list[dict]:
    events: list[dict] = []
    if not path or not Path(path).exists():
        print(f"[merger] {source_tag} file not found — skipping: {path}")
        return events
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
                ev["_source"] = source_tag
                events.append(ev)
            except json.JSONDecodeError as e:
                print(f"[merger] bad JSON in {source_tag}: {e}")
    return events


def merge_and_sort(
    zone_events_path: str | None,
    entry_events_path: str | None,
    reid_events_path: str | None,
) -> list[dict]:
    """
    Merge all three event streams and sort chronologically by timestamp.
    Returns a flat list ready for sequential processing.
    """
    all_events: list[dict] = []
    all_events.extend(_load_jsonl(zone_events_path,  "L3"))
    all_events.extend(_load_jsonl(entry_events_path, "L4"))
    all_events.extend(_load_jsonl(reid_events_path,  "L5"))

    all_events.sort(key=lambda e: e.get("timestamp", ""))

    counts = {src: sum(1 for e in all_events if e["_source"] == src) for src in ("L3","L4","L5")}
    print(f"[merger] loaded  L3={counts['L3']}  L4={counts['L4']}  L5={counts['L5']}  "
          f"total={len(all_events)}")
    return all_events
