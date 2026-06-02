import cv2
from pathlib import Path

VIDEO_EXTENSIONS = {".mp4", ".mov", ".hevc", ".mkv", ".avi"}

_TYPE_KEYWORDS = ["billing", "entry", "zone"]


def detect_camera_type(stem: str) -> str:
    name = stem.lower()
    for kw in _TYPE_KEYWORDS:
        if kw in name:
            return kw
    return "zone"


def extract_frames(store_input_dir: str, frames_output_dir: str) -> list[dict]:
    """Extract one representative frame per camera video.

    Returns a list of camera metadata dicts consumed by the rest of the pipeline.
    """
    input_dir = Path(store_input_dir)
    output_dir = Path(frames_output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    type_counters: dict[str, int] = {}
    cameras = []

    for video_file in sorted(input_dir.iterdir()):
        if video_file.suffix.lower() not in VIDEO_EXTENSIONS:
            continue

        cam_type = detect_camera_type(video_file.stem)
        count = type_counters.get(cam_type, 0) + 1
        type_counters[cam_type] = count

        cam_id = f"CAM_{cam_type.upper()}_{count:02d}"
        frame_path = output_dir / f"{cam_id}.jpg"

        frame, w, h = _grab_frame(str(video_file), target_seconds=5.0)
        if frame is None:
            print(f"  [WARN] Could not read frame from {video_file.name}")
            continue

        cv2.imwrite(str(frame_path), frame)
        print(f"  [OK]   {cam_id} <- {video_file.name}  ({w}x{h})")

        cameras.append({
            "camera_id": cam_id,
            "camera_type": cam_type,
            "source_video": video_file.name,
            "frame_path": str(frame_path),
            "frame_width": w,
            "frame_height": h,
        })

    return cameras


def _grab_frame(video_path: str, target_seconds: float = 5.0):
    """Return (frame ndarray, width, height); falls back to first frame on seek failure."""
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        return None, 0, 0

    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    cap.set(cv2.CAP_PROP_POS_FRAMES, int(fps * target_seconds))
    ret, frame = cap.read()

    if not ret:
        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        ret, frame = cap.read()

    cap.release()

    if not ret:
        return None, 0, 0

    h, w = frame.shape[:2]
    return frame, w, h
