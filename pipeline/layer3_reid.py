"""
Layer 3 — Re-ID (Cross-Camera / Re-entry)
Model:  OSNet x1.0 (torchreid)
Input:  ../input/tracked_bboxes/<stem>_tracks.json   (Layer 2 output, same folder name)
        ../input/raw_video_frame/<stem>.mp4           (original video, for cropping)
        ../input/embedding_database/db.json           (persistent visitor DB, auto-created)
Output: ../output/visitor_ids/<stem>_visitors.json
        ../input/cropped_person_image/<crops>         (saved for inspection)

Output schema per item:
  {
    "track_id":            7,
    "visitor_id":          "VIS_c8a2f1",
    "is_new_visitor":      true,
    "reentry":             false,
    "embedding_distance":  0.23,
    "matched_visitor_id":  null,
    "frame_id":            450
  }

Install:  pip install torchreid
          (optional Re-ID weights: download osnet_x1_0_market.pth from torchreid model zoo
           and place in model/ — ImageNet pretrained weights are used if absent)
"""

from __future__ import annotations

from pathlib import Path
from collections import defaultdict
from uuid import uuid4
import json
import numpy as np
import cv2
import torch
from torchvision import transforms
from PIL import Image

# ── Paths ─────────────────────────────────────────────────────────────────────
ROOT             = Path(__file__).parent.parent
INPUT_TRACKS_DIR = ROOT / "input"  / "tracked_bboxes"
INPUT_VIDEO_DIR  = ROOT / "input"  / "raw_video_frame"
INPUT_CROPS_DIR  = ROOT / "input"  / "cropped_person_image"
INPUT_DB_DIR     = ROOT / "input"  / "embedding_database"
OUTPUT_DIR       = ROOT / "output" / "visitor_ids"

DB_PATH          = INPUT_DB_DIR / "db.json"
REID_WEIGHTS     = Path(__file__).parent / "osnet_x1_0_market.pth"   # optional

# ── Config ────────────────────────────────────────────────────────────────────
DISTANCE_THRESHOLD = 0.4          # cosine distance: < threshold → same person
DEVICE             = "cuda" if torch.cuda.is_available() else "cpu"
VIDEO_EXTS         = {".mp4", ".avi", ".mov"}

TRANSFORM = transforms.Compose([
    transforms.Resize((256, 128)),
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
])


# ── Model ─────────────────────────────────────────────────────────────────────
def load_osnet() -> torch.nn.Module:
    import torchreid

    model = torchreid.models.build_model(
        name="osnet_x1_0",
        num_classes=1,          # ignored in eval mode; model returns 512-dim feature
        pretrained=True,        # ImageNet weights if Re-ID weights file is absent
    )
    if REID_WEIGHTS.exists():
        torchreid.utils.load_pretrained_weights(model, str(REID_WEIGHTS))
        print(f"[Layer 3] Loaded Re-ID weights from {REID_WEIGHTS.name}")
    else:
        print("[Layer 3] Re-ID weights not found — using ImageNet pretrained OSNet")

    model.eval().to(DEVICE)
    return model


def extract_embedding(model: torch.nn.Module, bgr_crop: np.ndarray) -> np.ndarray:
    rgb = cv2.cvtColor(bgr_crop, cv2.COLOR_BGR2RGB)
    img = Image.fromarray(rgb)
    tensor = TRANSFORM(img).unsqueeze(0).to(DEVICE)
    with torch.no_grad():
        feat = model(tensor)
    feat = feat.squeeze().cpu().numpy().astype(np.float32)
    norm = np.linalg.norm(feat)
    return feat / (norm + 1e-8)


# ── Embedding DB ──────────────────────────────────────────────────────────────
def load_db() -> dict[str, np.ndarray]:
    if DB_PATH.exists():
        with open(DB_PATH) as f:
            raw = json.load(f)
        return {vid: np.array(emb, dtype=np.float32) for vid, emb in raw.items()}
    return {}


def save_db(db: dict[str, np.ndarray]) -> None:
    INPUT_DB_DIR.mkdir(parents=True, exist_ok=True)
    DB_PATH.write_text(json.dumps(
        {vid: emb.tolist() for vid, emb in db.items()}, indent=2
    ))


# ── Matching ──────────────────────────────────────────────────────────────────
def match_or_create(
    embedding: np.ndarray,
    db: dict[str, np.ndarray],
    exit_set: set[str],
) -> tuple[str, bool, bool, float, str | None]:
    """
    Returns (visitor_id, is_new, reentry, embedding_distance, matched_visitor_id).
    """
    best_id   = None
    best_dist = float("inf")

    for vid, stored in db.items():
        dist = float(1.0 - np.dot(embedding, stored))
        if dist < best_dist:
            best_dist, best_id = dist, vid

    if best_dist < DISTANCE_THRESHOLD:
        reentry = best_id in exit_set
        return best_id, False, reentry, round(best_dist, 4), best_id

    new_id     = f"VIS_{uuid4().hex[:6]}"
    db[new_id] = embedding
    return new_id, True, False, round(best_dist, 4), None


# ── Per-video processing ──────────────────────────────────────────────────────
def process_video(
    tracks_path: Path,
    video_path: Path,
    model: torch.nn.Module,
    db: dict[str, np.ndarray],
) -> list[dict]:
    with open(tracks_path) as f:
        tracks: list[dict] = json.load(f)

    by_frame: dict[int, list[dict]] = defaultdict(list)
    for t in tracks:
        by_frame[t["frame_id"]].append(t)

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    results:          list[dict]           = []
    exit_set:         set[str]             = set()
    track_to_visitor: dict[int, str]       = {}
    active_tracks:    set[int]             = set()

    frame_id = 0
    while True:
        ret, frame = cap.read()
        if not ret:
            break

        frame_tracks = by_frame.get(frame_id, [])
        current_ids  = {t["track_id"] for t in frame_tracks}

        # mark disappeared tracks as exited
        for gone_id in active_tracks - current_ids:
            if gone_id in track_to_visitor:
                exit_set.add(track_to_visitor[gone_id])

        active_tracks = current_ids

        for t in frame_tracks:
            tid = t["track_id"]
            x1, y1, x2, y2 = (
                max(0, int(t["bbox"][0])),
                max(0, int(t["bbox"][1])),
                min(frame.shape[1], int(t["bbox"][2])),
                min(frame.shape[0], int(t["bbox"][3])),
            )
            if x2 <= x1 or y2 <= y1:
                continue

            crop = frame[y1:y2, x1:x2]

            # save crop for inspection
            crop_file = INPUT_CROPS_DIR / f"frame{frame_id:06d}_track{tid}.jpg"
            cv2.imwrite(str(crop_file), crop)

            if tid in track_to_visitor:
                # already assigned — reuse without re-embedding
                visitor_id  = track_to_visitor[tid]
                is_new      = False
                reentry     = False
                emb_dist    = 0.0
                matched_id  = visitor_id
            else:
                emb = extract_embedding(model, crop)
                visitor_id, is_new, reentry, emb_dist, matched_id = match_or_create(
                    emb, db, exit_set
                )
                track_to_visitor[tid] = visitor_id

            results.append({
                "track_id":            tid,
                "visitor_id":          visitor_id,
                "is_new_visitor":      is_new,
                "reentry":             reentry,
                "embedding_distance":  emb_dist,
                "matched_visitor_id":  matched_id if not is_new else None,
                "frame_id":            frame_id,
            })

        frame_id += 1

    cap.release()
    return results


# ── Entry point ───────────────────────────────────────────────────────────────
def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    INPUT_CROPS_DIR.mkdir(parents=True, exist_ok=True)

    track_files = sorted(INPUT_TRACKS_DIR.glob("*_tracks.json"))
    if not track_files:
        print(f"[Layer 3] No track JSONs found in {INPUT_TRACKS_DIR}")
        print(f"[Layer 3] Copy Layer 2 output (output/tracked_bboxes/) into input/tracked_bboxes/")
        return

    model = load_osnet()
    db    = load_db()

    for tracks_path in track_files:
        stem = tracks_path.stem.replace("_tracks", "")

        # find matching video (try all extensions)
        video_path = next(
            (INPUT_VIDEO_DIR / f"{stem}{ext}" for ext in VIDEO_EXTS
             if (INPUT_VIDEO_DIR / f"{stem}{ext}").exists()),
            None,
        )
        if video_path is None:
            print(f"[Layer 3] No video found for {tracks_path.name} — skipping")
            continue

        print(f"[Layer 3] Re-ID for {tracks_path.name} + {video_path.name} …")
        results = process_video(tracks_path, video_path, model, db)

        out = OUTPUT_DIR / f"{stem}_visitors.json"
        out.write_text(json.dumps(results, indent=2))
        print(f"[Layer 3] → {len(results)} entries  →  {out.relative_to(ROOT)}")

    save_db(db)
    print(f"[Layer 3] Embedding DB saved to {DB_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
