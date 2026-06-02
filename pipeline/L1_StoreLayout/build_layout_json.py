def bbox_to_polygon(bbox: list) -> list:
    """Convert [x1, y1, x2, y2] to a 4-corner polygon list."""
    x1, y1, x2, y2 = [int(v) for v in bbox]
    return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]


def build_layout_json(
    store_id: str,
    cameras: list[dict],
    open_time: str = "10:00",
    close_time: str = "22:00",
) -> dict:
    """Assemble the final store_layout.json structure from per-camera calibration results.

    Each camera dict must have:
      camera_id, camera_type, frame_width, frame_height, calibration (from calibrate_camera)
    """
    camera_entries = []
    boh_zones: list[str] = []
    billing_camera_id: str | None = None
    entry_camera_id: str | None = None

    for cam in cameras:
        cam_id = cam["camera_id"]
        cam_type = cam["camera_type"]
        calibration = cam.get("calibration", {})
        fw = cam["frame_width"]
        fh = cam["frame_height"]

        zones = []
        for vz in calibration.get("visible_zones", []):
            bbox = vz.get("pixel_bbox") or [0, 0, fw, fh]
            zones.append({
                "zone_id": vz["zone_id"],
                "zone_name": vz["zone_name"],
                "zone_type": vz["zone_type"],
                "is_revenue_zone": vz.get("is_revenue_zone", False),
                "polygon": bbox_to_polygon(bbox),
            })
            if vz["zone_type"] == "boh" and vz["zone_id"] not in boh_zones:
                boh_zones.append(vz["zone_id"])

        cam_entry: dict = {
            "camera_id": cam_id,
            "camera_type": cam_type,
            "frame_width": fw,
            "frame_height": fh,
            "zones": zones,
        }

        if cam_type == "entry":
            entry_camera_id = cam_id
            entry_line = calibration.get("entry_line")
            if entry_line:
                cam_entry["entry_line"] = entry_line
                cam_entry["entry_direction"] = calibration.get("entry_direction", "bottom_to_top")
        elif cam_type == "billing":
            billing_camera_id = cam_id

        camera_entries.append(cam_entry)

    return {
        "store_id": store_id,
        "open_hours": {"open": open_time, "close": close_time},
        "cameras": camera_entries,
        "boh_zones": boh_zones,
        "billing_camera_id": billing_camera_id,
        "entry_camera_id": entry_camera_id,
    }
