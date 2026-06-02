"""
Zone Annotation Tool — L1 Store Layout Builder
================================================
Usage (standalone):
    python annotate_zones.py --layout output/Store1/store_layout.json \
                             --frames  output/Store1/frames

Controls:
    LEFT CLICK   add a polygon point
    RIGHT CLICK  undo last point
    ENTER        confirm current zone polygon (needs ≥ 3 points)
    A            add a brand-new zone  (prompts name + type in terminal)
    D            delete last confirmed zone
    N            skip zone — keep existing polygon unchanged
    R            reset / clear points for current zone
    Z            toggle zoom-inset under cursor
    S            save layout JSON and close window
    Q            quit this camera without saving
"""

import argparse
import copy
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

# ── colour palette (BGR) ────────────────────────────────────────────────────
ZONE_COLOURS = {
    "shelf":   (50,  205, 50),
    "billing": (200, 120, 30),
    "entry":   (30,  210, 220),
    "boh":     (50,  50,  210),
    "floor":   (160, 160, 160),
}
DEFAULT_COLOUR  = (180, 180, 0)
POINT_COLOUR    = (0,   255, 255)
EXISTING_COLOUR = (0,   180, 255)
LINE_COLOUR     = (255, 255, 255)
ENTRY_LINE_CLR  = (0,   255, 255)
FONT            = cv2.FONT_HERSHEY_SIMPLEX

ZONE_TYPES = ["shelf", "billing", "entry", "boh", "floor"]


def _colour(zone: dict) -> tuple:
    return ZONE_COLOURS.get(zone.get("zone_type", ""), DEFAULT_COLOUR)


# ── rendering ────────────────────────────────────────────────────────────────

def _label(img, text, cx, cy, colour):
    scale, thick = 0.52, 1
    (tw, th), bl = cv2.getTextSize(text, FONT, scale, thick)
    x = int(max(4, min(cx - tw // 2, img.shape[1] - tw - 4)))
    y = int(max(th + 6, min(cy + th // 2, img.shape[0] - bl - 4)))
    cv2.rectangle(img, (x - 3, y - th - 3), (x + tw + 3, y + bl + 1), (15, 15, 15), -1)
    cv2.putText(img, text, (x, y), FONT, scale, colour, thick, cv2.LINE_AA)


def draw_state(base, confirmed, current_pts, current_zone,
               remaining_count, entry_line=None, entry_dir=None, zoom_pos=None):
    img = base.copy()
    h, w = img.shape[:2]
    overlay = img.copy()

    # confirmed zones
    for zone in confirmed:
        pts = np.array(zone["polygon"], dtype=np.int32)
        col = _colour(zone)
        cv2.fillPoly(overlay, [pts], col)
        cv2.polylines(img, [pts], True, col, 2, cv2.LINE_AA)
        cx, cy = int(pts[:, 0].mean()), int(pts[:, 1].mean())
        _label(img, zone["zone_name"], cx, cy, col)

    cv2.addWeighted(overlay, 0.18, img, 0.82, 0, img)

    # entry line
    if entry_line:
        cv2.line(img, (entry_line["x1"], entry_line["y1"]),
                 (entry_line["x2"], entry_line["y2"]), ENTRY_LINE_CLR, 2, cv2.LINE_AA)
        _label(img, f"entry ({entry_dir})",
               entry_line["x1"] + 10, entry_line["y1"] + 30, ENTRY_LINE_CLR)

    # current zone being drawn
    if current_zone:
        col = _colour(current_zone)

        # existing polygon ghost
        if current_zone.get("polygon"):
            pts = np.array(current_zone["polygon"], dtype=np.int32)
            cv2.polylines(img, [pts], True, EXISTING_COLOUR, 1)

        # points placed so far
        for i, pt in enumerate(current_pts):
            cv2.circle(img, tuple(pt), 6, POINT_COLOUR, -1, cv2.LINE_AA)
            cv2.putText(img, str(i + 1), (pt[0] + 8, pt[1] - 8),
                        FONT, 0.38, POINT_COLOUR, 1, cv2.LINE_AA)
        if len(current_pts) >= 2:
            for i in range(len(current_pts) - 1):
                cv2.line(img, tuple(current_pts[i]), tuple(current_pts[i + 1]),
                         LINE_COLOUR, 1, cv2.LINE_AA)
        if len(current_pts) >= 3:
            cv2.line(img, tuple(current_pts[-1]), tuple(current_pts[0]),
                     LINE_COLOUR, 1, cv2.LINE_AA)
            pts = np.array(current_pts, dtype=np.int32)
            tmp = img.copy()
            cv2.fillPoly(tmp, [pts], col)
            cv2.addWeighted(tmp, 0.28, img, 0.72, 0, img)

    # HUD
    lines: list[str] = []
    if current_zone:
        lines.append(f"  Zone: {current_zone['zone_name']}  [{current_zone['zone_type']}]"
                     f"  —  {remaining_count} remaining")
        lines.append("  ENTER=confirm  R=reset  N=skip  A=add new  D=del last  Z=zoom  S=save  Q=quit")
    else:
        lines.append(f"  {len(confirmed)} zones done. "
                     "A=add more  D=del last  S=save+quit  Q=quit")
    for i, ln in enumerate(lines):
        y = 20 + i * 22
        cv2.rectangle(img, (0, y - 15), (len(ln) * 8 + 4, y + 5), (0, 0, 0), -1)
        cv2.putText(img, ln, (4, y), FONT, 0.50, (255, 255, 255), 1, cv2.LINE_AA)

    # zoom inset
    if zoom_pos:
        zx, zy = zoom_pos
        r = 80
        x1, y1 = max(0, zx - r), max(0, zy - r)
        x2, y2 = min(w, zx + r), min(h, zy + r)
        crop = base[y1:y2, x1:x2]
        if crop.size:
            zim = cv2.resize(crop, (280, 280), interpolation=cv2.INTER_LINEAR)
            ch, cw = zim.shape[:2]
            cv2.line(zim, (cw // 2, 0), (cw // 2, ch), (0, 255, 255), 1)
            cv2.line(zim, (0, ch // 2), (cw, ch // 2), (0, 255, 255), 1)
            img[h - 294:h - 14, w - 294:w - 14] = zim
            cv2.rectangle(img, (w - 294, h - 294), (w - 14, h - 14), (255, 255, 255), 1)

    return img


# ── main annotation loop ─────────────────────────────────────────────────────

def annotate_camera(image_path: str, camera_data: dict) -> dict:
    """
    Interactive polygon annotation for one camera.
    Returns updated camera_data (zones list replaced with annotated polygons).
    """
    img = cv2.imread(image_path)
    if img is None:
        print(f"  [ERROR] Cannot read: {image_path}")
        return camera_data

    h, w = img.shape[:2]
    cam_id = camera_data["camera_id"]
    camera_data["frame_width"]  = w
    camera_data["frame_height"] = h

    zones      = copy.deepcopy(camera_data.get("zones", []))
    confirmed: list[dict] = []
    zone_idx   = 0
    current_pts: list[list[int]] = []
    zoom_on    = False
    zoom_pos   = None
    saved      = False

    win = f"Annotate — {cam_id}  |  {Path(image_path).name}"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(win, min(w, 1440), min(h + 60, 960))

    def on_mouse(event, x, y, flags, _):
        nonlocal zoom_pos
        zoom_pos = (x, y) if zoom_on else None
        if event == cv2.EVENT_LBUTTONDOWN:
            current_pts.append([x, y])
        elif event == cv2.EVENT_RBUTTONDOWN and current_pts:
            current_pts.pop()

    cv2.setMouseCallback(win, on_mouse)

    print(f"\n{'='*60}")
    print(f"  {cam_id}   ({w}×{h})   {len(zones)} pre-defined zone(s)")
    if zones:
        print(f"  Zones: {[z['zone_name'] for z in zones]}")
    else:
        print("  No pre-existing zones — press A to add them.")
    print(f"{'='*60}")

    while True:
        current_zone = zones[zone_idx] if zone_idx < len(zones) else None
        remaining    = len(zones) - zone_idx

        frame = draw_state(img, confirmed, current_pts, current_zone,
                           remaining, camera_data.get("entry_line"),
                           camera_data.get("entry_direction"), zoom_pos)
        cv2.imshow(win, frame)
        key = cv2.waitKey(20) & 0xFF

        # ENTER — confirm polygon
        if key == 13:
            if current_zone is None:
                break
            if len(current_pts) >= 3:
                current_zone = copy.deepcopy(current_zone)
                current_zone["polygon"] = [list(p) for p in current_pts]
                confirmed.append(current_zone)
                print(f"  ✓  {current_zone['zone_name']}  ({len(current_pts)} pts)")
                current_pts.clear()
                zone_idx += 1
            else:
                print("  ⚠  Need at least 3 points.")

        # N — skip, keep existing polygon
        elif key == ord('n') and current_zone:
            confirmed.append(copy.deepcopy(current_zone))
            print(f"  →  Kept: {current_zone['zone_name']}")
            current_pts.clear()
            zone_idx += 1

        # R — reset points
        elif key == ord('r'):
            current_pts.clear()

        # A — add new zone
        elif key == ord('a'):
            cv2.destroyWindow(win)
            name = input("\n  New zone name  : ").strip()
            if name:
                print(f"  Zone types: {ZONE_TYPES}")
                ztype = input("  Zone type      : ").strip().lower()
                if ztype not in ZONE_TYPES:
                    ztype = "shelf"
                new_zone = {
                    "zone_id":        name.upper().replace(" ", "_"),
                    "zone_name":      name,
                    "zone_type":      ztype,
                    "is_revenue_zone": ztype in ("shelf", "billing"),
                    "polygon":        [],
                }
                zones.append(new_zone)
                zone_idx = len(zones) - 1
                print(f"  + Added zone '{name}' [{ztype}] — draw its polygon now.")
            cv2.namedWindow(win, cv2.WINDOW_NORMAL)
            cv2.resizeWindow(win, min(w, 1440), min(h + 60, 960))
            cv2.setMouseCallback(win, on_mouse)

        # D — delete last confirmed zone
        elif key == ord('d'):
            if confirmed:
                removed = confirmed.pop()
                print(f"  ✗  Removed: {removed['zone_name']}")
            else:
                print("  Nothing to delete.")

        # Z — toggle zoom
        elif key == ord('z'):
            zoom_on = not zoom_on
            zoom_pos = None

        # S — save and exit
        elif key == ord('s'):
            # append any unvisited zones unchanged
            for z in zones[zone_idx:]:
                confirmed.append(copy.deepcopy(z))
            camera_data["zones"] = confirmed
            saved = True
            print(f"  💾  Saved {len(confirmed)} zone(s) for {cam_id}")
            break

        # Q — quit without saving
        elif key == ord('q'):
            print(f"  ✗  Quit without saving {cam_id}.")
            break

    cv2.destroyWindow(win)
    if saved:
        camera_data["zones"] = confirmed
    return camera_data


# ── entry line annotation ─────────────────────────────────────────────────────

def annotate_entry_line(image_path: str, camera_data: dict) -> dict:
    """Draw a 2-point entry threshold line for entry cameras."""
    img = cv2.imread(image_path)
    if img is None:
        return camera_data

    h, w    = img.shape[:2]
    cam_id  = camera_data["camera_id"]
    pts: list[tuple[int, int]] = []
    existing = camera_data.get("entry_line")
    direction = camera_data.get("entry_direction", "bottom_to_top")
    DIRS = ["bottom_to_top", "top_to_bottom", "left_to_right", "right_to_left"]

    win = f"Entry Line — {cam_id}"
    cv2.namedWindow(win, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(win, min(w, 1440), min(h + 60, 960))

    def on_mouse(event, x, y, flags, _):
        if event == cv2.EVENT_LBUTTONDOWN and len(pts) < 2:
            pts.append((x, y))

    cv2.setMouseCallback(win, on_mouse)

    print(f"\n  Entry line for {cam_id}:")
    print(f"  Click 2 points (start → end).  D=cycle direction  ENTER=ok  R=reset  Q=skip")

    while True:
        disp = img.copy()
        if existing:
            cv2.line(disp, (existing["x1"], existing["y1"]),
                     (existing["x2"], existing["y2"]), (0, 180, 180), 1)
            _label(disp, "existing", existing["x1"], existing["y1"] - 10, (0, 180, 180))
        for p in pts:
            cv2.circle(disp, p, 7, ENTRY_LINE_CLR, -1, cv2.LINE_AA)
        if len(pts) == 2:
            cv2.line(disp, pts[0], pts[1], ENTRY_LINE_CLR, 2, cv2.LINE_AA)

        hud = f"  Direction: {direction}   pts: {len(pts)}/2   D=flip  ENTER=ok  R=reset  Q=skip"
        cv2.rectangle(disp, (0, 0), (len(hud) * 8, 22), (0, 0, 0), -1)
        cv2.putText(disp, hud, (4, 16), FONT, 0.50, (255, 255, 255), 1, cv2.LINE_AA)
        cv2.imshow(win, disp)
        key = cv2.waitKey(20) & 0xFF

        if key == 13 and len(pts) == 2:
            camera_data["entry_line"] = {
                "x1": pts[0][0], "y1": pts[0][1],
                "x2": pts[1][0], "y2": pts[1][1],
            }
            camera_data["entry_direction"] = direction
            print(f"  ✓  Entry line: {pts[0]} → {pts[1]}  ({direction})")
            break
        elif key == ord('d'):
            direction = DIRS[(DIRS.index(direction) + 1) % len(DIRS)]
            print(f"  Direction → {direction}")
        elif key == ord('r'):
            pts.clear()
        elif key == ord('q'):
            break

    cv2.destroyWindow(win)
    return camera_data


# ── CLI entry point ───────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Interactive zone annotator")
    ap.add_argument("--layout",  required=True, help="Path to store_layout.json")
    ap.add_argument("--frames",  required=True, help="Folder containing camera frame JPGs")
    ap.add_argument("--camera",  default=None,  help="Annotate only this camera_id")
    ap.add_argument("--output",  default=None,  help="Output path (default: overwrite with backup)")
    args = ap.parse_args()

    with open(args.layout, encoding="utf-8") as f:
        layout = json.load(f)

    cameras = layout["cameras"]
    if args.camera:
        cameras = [c for c in cameras if c["camera_id"] == args.camera]
        if not cameras:
            print(f"[ERROR] Camera '{args.camera}' not found.")
            sys.exit(1)

    exts = (".jpg", ".jpeg", ".png", ".bmp")

    def find_frame(cam_id: str) -> str | None:
        for ext in exts:
            p = os.path.join(args.frames, cam_id + ext)
            if os.path.exists(p):
                return p
        for fn in os.listdir(args.frames):
            if cam_id.lower() in fn.lower() and fn.lower().endswith(exts):
                return os.path.join(args.frames, fn)
        return None

    for cam in cameras:
        cam_id = cam["camera_id"]
        img_path = find_frame(cam_id)
        if not img_path:
            print(f"\n[SKIP] No frame for {cam_id} in {args.frames}")
            continue

        cam_updated = annotate_camera(img_path, cam)
        if cam_updated.get("camera_type") == "entry":
            cam_updated = annotate_entry_line(img_path, cam_updated)

        for i, c in enumerate(layout["cameras"]):
            if c["camera_id"] == cam_id:
                layout["cameras"][i] = cam_updated
                break

    out_path = args.output or args.layout
    if out_path == args.layout:
        bk = args.layout.replace(".json",
             f"_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json")
        with open(bk, "w", encoding="utf-8") as f:
            json.dump(layout, f, indent=2)
        print(f"\n  Backup → {bk}")

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(layout, f, indent=2)
    print(f"  ✅  Saved → {out_path}")


if __name__ == "__main__":
    main()
