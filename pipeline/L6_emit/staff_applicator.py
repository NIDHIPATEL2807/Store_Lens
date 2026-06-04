"""
Builds the set of (camera_id, track_id) pairs that are staff.

Applied retroactively — if a track is flagged at frame 500,
all earlier events for that track also get is_staff: true.
"""


class StaffApplicator:
    def __init__(self):
        self._staff_keys: set[tuple] = set()   # (camera_id, track_id)

    def build(self, all_events: list[dict]) -> None:
        for ev in all_events:
            if ev.get("event_type") == "STAFF_FLAGGED":
                cam = ev.get("camera_id")
                tid = ev.get("metadata", {}).get("track_id")
                if cam and tid is not None:
                    self._staff_keys.add((cam, tid))
        print(f"[staff_applicator] {len(self._staff_keys)} staff track(s) registered")

    def is_staff(self, camera_id: str, track_id: int | None) -> bool:
        if track_id is None:
            return False
        return (camera_id, track_id) in self._staff_keys
