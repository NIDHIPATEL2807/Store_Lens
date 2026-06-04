"""
POS transaction correlation and BILLING_QUEUE_ABANDON detection.

How it works:
  - As events stream through, billing zone entries/exits are recorded.
  - After all events are processed, each POS transaction is matched
    against the set of visitors who were in a billing zone in the
    preceding POS_WINDOW_MINUTES.
  - Any billing visit with no POS transaction within POS_WINDOW_MINUTES
    of the exit → BILLING_QUEUE_ABANDON event.
"""

import csv
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

POS_WINDOW_MINUTES = 5


def _parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))

def _fmt_ts(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


@dataclass
class BillingVisit:
    visitor_id:  str
    camera_id:   str
    zone_id:     str
    store_id:    str
    enter_ts:    datetime
    exit_ts:     datetime | None = None
    track_id:    int | None = None
    confidence:  float = 0.0


class POSCorrelator:
    def __init__(self, billing_zone_ids: set[str]):
        self._billing_zones = billing_zone_ids
        self._active: dict[str, BillingVisit] = {}   # visitor_id → open visit
        self._completed: list[BillingVisit] = []

    def is_billing_zone(self, zone_id: str | None) -> bool:
        if not zone_id:
            return False
        return zone_id.upper() in {z.upper() for z in self._billing_zones}

    def on_event(self, ev: dict) -> None:
        """Feed each event as it is processed."""
        etype   = ev.get("event_type", "")
        vid     = ev.get("visitor_id")
        zone_id = ev.get("zone_id")

        if not vid or not self.is_billing_zone(zone_id):
            return
        if ev.get("is_staff"):
            return

        if etype in ("ZONE_ENTER", "BILLING_QUEUE_JOIN"):
            self._active[vid] = BillingVisit(
                visitor_id = vid,
                camera_id  = ev.get("camera_id", ""),
                zone_id    = zone_id,
                store_id   = ev.get("store_id", ""),
                enter_ts   = _parse_ts(ev["timestamp"]),
                track_id   = ev.get("metadata", {}).get("track_id"),
                confidence = ev.get("confidence", 0.0),
            )
        elif etype == "ZONE_EXIT":
            visit = self._active.pop(vid, None)
            if visit:
                visit.exit_ts = _parse_ts(ev["timestamp"])
                self._completed.append(visit)

    def load_pos_csv(self, path: str | None) -> list[dict]:
        if not path or not Path(path).exists():
            print(f"[pos_correlator] POS file not found: {path} — skipping conversion")
            return []
        transactions = []
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    transactions.append({
                        "store_id":    row.get("store_id", ""),
                        "timestamp":   row["timestamp"],
                        "basket_value": float(row.get("basket_value", 0)),
                        "_ts":         _parse_ts(row["timestamp"]),
                    })
                except Exception as e:
                    print(f"[pos_correlator] bad POS row: {e}")
        print(f"[pos_correlator] loaded {len(transactions)} POS transactions")
        return transactions

    def correlate(self, transactions: list[dict], store_id: str) -> set[str]:
        """
        Returns the set of visitor_ids who converted (had a POS transaction
        within POS_WINDOW_MINUTES of being in a billing zone).
        """
        converted: set[str] = set()
        window = timedelta(minutes=POS_WINDOW_MINUTES)

        for txn in transactions:
            if txn.get("store_id") and txn["store_id"] != store_id:
                continue
            txn_ts = txn["_ts"]
            lookback = txn_ts - window

            for visit in self._completed:
                if visit.store_id and visit.store_id != store_id:
                    continue
                # Visitor was in billing zone between lookback and txn_ts
                if lookback <= visit.enter_ts <= txn_ts:
                    converted.add(visit.visitor_id)

        print(f"[pos_correlator] {len(converted)} converted visitor(s)")
        return converted

    def get_abandon_events(
        self,
        transactions: list[dict],
        store_id: str,
        session_seq_fn,   # callable(visitor_id) → next seq int
    ) -> list[dict]:
        """
        Emit BILLING_QUEUE_ABANDON for every billing visit that ended
        without a POS transaction following within POS_WINDOW_MINUTES.
        """
        window = timedelta(minutes=POS_WINDOW_MINUTES)
        abandon_events: list[dict] = []

        for visit in self._completed:
            if not visit.exit_ts:
                continue
            if visit.store_id and visit.store_id != store_id:
                continue

            exit_ts = visit.exit_ts
            deadline = exit_ts + window

            matched = any(
                exit_ts <= txn["_ts"] <= deadline
                and (not txn.get("store_id") or txn["store_id"] == store_id)
                for txn in transactions
            )

            if not matched:
                seq = session_seq_fn(visit.visitor_id)
                abandon_events.append({
                    "event_id":   str(uuid.uuid4()),
                    "store_id":   visit.store_id or store_id,
                    "camera_id":  visit.camera_id,
                    "visitor_id": visit.visitor_id,
                    "event_type": "BILLING_QUEUE_ABANDON",
                    "timestamp":  _fmt_ts(exit_ts),
                    "zone_id":    visit.zone_id,
                    "dwell_ms":   int((exit_ts - visit.enter_ts).total_seconds() * 1000),
                    "is_staff":   False,
                    "confidence": visit.confidence,
                    "converted":  False,
                    "metadata": {
                        "queue_depth":  None,
                        "sku_zone":     visit.zone_id,
                        "session_seq":  seq,
                        "group_id":     None,
                        "group_size":   None,
                        "track_id":     visit.track_id,
                    },
                })

        print(f"[pos_correlator] {len(abandon_events)} BILLING_QUEUE_ABANDON event(s)")
        return abandon_events
