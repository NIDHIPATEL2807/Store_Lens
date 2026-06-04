"""
Line crossing utilities for entry/exit detection.
Provides side-of-line classification and direction resolution.
"""

from shapely.geometry import LineString


def build_entry_line(line_dict: dict) -> LineString:
    return LineString([(line_dict["x1"], line_dict["y1"]),
                       (line_dict["x2"], line_dict["y2"])])


def crossed_line(prev_centroid, curr_centroid, entry_line: LineString) -> bool:
    """True if the movement vector between two centroids crosses the entry line."""
    if prev_centroid is None:
        return False
    movement = LineString([prev_centroid, curr_centroid])
    return movement.crosses(entry_line)


def get_crossing_direction(prev_centroid, curr_centroid, entry_direction: str) -> str:
    """Map the movement vector to ENTRY or EXIT based on the configured crossing direction."""
    dy = curr_centroid[1] - prev_centroid[1]
    dx = curr_centroid[0] - prev_centroid[0]
    if entry_direction == "top_to_bottom":
        return "ENTRY" if dy > 0 else "EXIT"
    if entry_direction == "bottom_to_top":
        return "ENTRY" if dy < 0 else "EXIT"
    if entry_direction == "left_to_right":
        return "ENTRY" if dx > 0 else "EXIT"
    if entry_direction == "right_to_left":
        return "ENTRY" if dx < 0 else "EXIT"
    return "ENTRY"


def get_side(centroid, entry_line_dict: dict, entry_direction: str) -> str:
    """
    Which side of the entry line is the centroid on?

    Uses the midpoint of the entry line as the split reference so the logic
    works for any line orientation without requiring axis-aligned geometry.
    """
    x1, y1 = entry_line_dict["x1"], entry_line_dict["y1"]
    x2, y2 = entry_line_dict["x2"], entry_line_dict["y2"]
    cx, cy = centroid
    mid_y = (y1 + y2) / 2
    mid_x = (x1 + x2) / 2

    if entry_direction == "top_to_bottom":
        return "inside" if cy > mid_y else "outside"
    if entry_direction == "bottom_to_top":
        return "inside" if cy < mid_y else "outside"
    if entry_direction == "left_to_right":
        return "inside" if cx > mid_x else "outside"
    if entry_direction == "right_to_left":
        return "inside" if cx < mid_x else "outside"
    return "outside"


def is_inside_store(centroid, entry_line_dict: dict, entry_direction: str) -> bool:
    return get_side(centroid, entry_line_dict, entry_direction) == "inside"
