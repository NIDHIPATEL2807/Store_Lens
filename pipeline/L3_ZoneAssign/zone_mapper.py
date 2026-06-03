"""
Zone mapping — point-in-polygon with priority resolution.

Driven entirely by the zones list from store_layout.json.
No store-specific or camera-specific logic is hardcoded here.
"""

from shapely.geometry import Point, Polygon
from shapely.errors import TopologicalError

# Lower number = higher priority when zones overlap.
# Any zone_type not in this table defaults to priority 99.
ZONE_PRIORITY: dict[str, int] = {
    "billing": 1,
    "entry":   2,
    "shelf":   3,
    "floor":   4,
    "boh":     5,
    "outside": 100,  # exclusion zones from L2 — should never reach L3, but safe to ignore
}


def build_camera_zone_index(layout: dict) -> dict[str, list[dict]]:
    """
    Parse store_layout.json and return {camera_id: [zone_dict, ...]}.

    Filters out degenerate zones (fewer than 3 polygon vertices) so callers
    never need to guard against empty-polygon crashes.  Preserves all cameras
    and all zone types — works for any layout without modification.
    """
    index: dict[str, list[dict]] = {}
    for cam in layout.get("cameras", []):
        cam_id = cam["camera_id"]
        valid: list[dict] = []
        for zone in cam.get("zones", []):
            pts = zone.get("polygon", [])
            if len(pts) < 3:
                continue
            valid.append(zone)
        index[cam_id] = valid
    return index


def get_zone_for_centroid(cx: int, cy: int, zones: list[dict]) -> dict | None:
    """
    Return the highest-priority zone whose polygon contains (cx, cy),
    or None when the centroid is in open floor / no annotated zone.

    When multiple zones match (overlapping polygons), the zone with the
    lowest ZONE_PRIORITY value wins — billing beats shelf beats floor.

    Degenerate or self-intersecting polygons are repaired with .buffer(0);
    if they still fail they are skipped so one bad polygon never crashes a clip.

    NOTE: zone_id is NOT unique in the layout (FLOOR appears multiple times).
    This function returns the full zone dict, not just the id, so the caller
    has access to zone_name, zone_type, is_revenue_zone etc.
    """
    point = Point(cx, cy)
    matches: list[dict] = []

    for zone in zones:
        try:
            poly = Polygon(zone["polygon"])
            if not poly.is_valid:
                poly = poly.buffer(0)   # attempt standard repair
            if not poly.is_valid or poly.is_empty:
                continue
            if poly.contains(point):
                matches.append(zone)
        except (TopologicalError, ValueError):
            continue

    if not matches:
        return None

    return min(matches, key=lambda z: ZONE_PRIORITY.get(z.get("zone_type", ""), 99))
