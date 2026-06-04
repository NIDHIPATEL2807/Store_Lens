#!/usr/bin/env python3
"""
L6 — Event Emitter (The Merge Layer)
======================================
Merges L3 / L4 / L5 outputs + POS data into one clean event stream
in the exact schema the API expects.

Jobs:
  1. Merge all events sorted by timestamp
  2. Assign visitor_id to zone events via L4 track→visitor mapping
  3. Apply is_staff flag retroactively from L5 STAFF_FLAGGED
  4. Handle REENTRY — suppress duplicate L4 ENTRY, continue session_seq
  5. POS correlation — mark converted visitors, emit BILLING_QUEUE_ABANDON
  6. session_seq numbering per visitor_id

Usage:
    python run_l6.py \\
        --zone_events   ../L3_ZoneAssign/output/CAM_ZONE_01_zone_events.jsonl \\
        --entry_events  ../L4_entry/output/store1_entry_events.jsonl \\
        --reid_events   ../L5_reid/output/reid_events.jsonl \\
        --pos_data      pos_transactions.csv \\
        --layout        ../L1_StoreLayout_selfannotate/output/Store\\ 1/store_layout.json \\
        --store_id      STORE_STORE_1 \\
        --output_dir    output/
"""

import argparse
import json
import sys
from pathlib import Path

from merger            import merge_and_sort
from visitor_mapper    import VisitorMapper
from staff_applicator  import StaffApplicator
from reentry_resolver  import ReentryResolver
from pos_correlator    import POSCorrelator
from session_sequencer import SessionSequencer
from event_builder     import build_event

# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_layout(layout_path: str) -> dict:
    with open(layout_path, encoding="utf-8") as f:
        return json.load(f)

def _build_zone_meta(layout: dict) -> dict:
    """zone_id → {zone_name, zone_type, is_revenue_zone}"""
    meta = {}
    for cam in layout.get("cameras", []):
        for z in cam.get("zones", []):
            zid = z.get("zone_id")
            if zid:
                meta[zid] = {
                    "zone_name":      z.get("zone_name", zid),
                    "zone_type":      z.get("zone_type", ""),
                    "is_revenue_zone": z.get("is_revenue_zone", False),
                }
    return meta

def _billing_zone_ids(layout: dict) -> set[str]:
    ids: set[str] = set()
    for cam in layout.get("cameras", []):
        for z in cam.get("zones", []):
            if z.get("zone_type") == "billing":
                ids.add(z["zone_id"])
    return ids

def _write_jsonl(path: Path, events: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zone_events",   default=None)
    ap.add_argument("--entry_events",  default=None)
    ap.add_argument("--reid_events",   default=None)
    ap.add_argument("--pos_data",      default=None)
    ap.add_argument("--layout",        required=True)
    ap.add_argument("--store_id",      default=None)
    ap.add_argument("--output_dir",    default="output")
    args = ap.parse_args()

    if not any([args.zone_events, args.entry_events, args.reid_events]):
        print("[ERROR] Provide at least one of --zone_events / --entry_events / --reid_events")
        sys.exit(1)

    # ── Load inputs ───────────────────────────────────────────────────────────
    layout       = _load_layout(args.layout)
    store_id     = args.store_id or layout.get("store_id", "STORE_UNKNOWN")
    zone_meta    = _build_zone_meta(layout)
    billing_ids  = _billing_zone_ids(layout)

    out_dir      = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path     = out_dir / "events.jsonl"
    reject_path  = out_dir / "rejected_events.jsonl"

    print(f"[L6] store={store_id}  billing_zones={billing_ids or '(none)'}")

    # ── Merge all events ──────────────────────────────────────────────────────
    all_events = merge_and_sort(args.zone_events, args.entry_events, args.reid_events)
    if not all_events:
        print("[L6] No events to process — exiting")
        return

    # ── Build lookup tables (two-pass) ────────────────────────────────────────
    visitor_mapper   = VisitorMapper();    visitor_mapper.build(all_events)
    staff_applicator = StaffApplicator();  staff_applicator.build(all_events)
    reentry_resolver = ReentryResolver();  reentry_resolver.build(all_events)
    session_seq      = SessionSequencer()
    pos_correlator   = POSCorrelator(billing_ids)

    # ── POS data ──────────────────────────────────────────────────────────────
    pos_transactions = pos_correlator.load_pos_csv(args.pos_data)

    # ── Main processing loop ──────────────────────────────────────────────────
    out_events:      list[dict] = []
    rejected_events: list[dict] = []
    seen_ids:        set[str]   = set()

    for raw in all_events:
        etype = raw.get("event_type", "")

        # L6 doesn't re-emit internal L5 events as-is (STAFF_FLAGGED handled via flag)
        if etype == "STAFF_FLAGGED":
            continue

        # Suppress L4 ENTRY that L5's REENTRY supersedes
        if reentry_resolver.is_suppressed(raw):
            continue

        cam    = raw.get("camera_id", "")
        meta   = raw.get("metadata") or {}
        tid    = meta.get("track_id")

        # ── Determine canonical visitor_id ────────────────────────────────────
        raw_vid = raw.get("visitor_id")

        if etype == "REENTRY":
            # visitor_id on REENTRY event is already the canonical (original) one
            visitor_id = raw_vid
            # Seed sequencer to continue from last known seq
            orig = visitor_id
            if orig and not session_seq.current(orig):
                prior = reentry_resolver.get_continuation(orig)
                if prior:
                    session_seq.set(orig, prior)
        elif raw_vid and not raw_vid.startswith("VIS_ZONE_"):
            # L4 or L5 already gave a clean visitor_id — resolve alias only
            visitor_id = visitor_mapper.resolve_alias(raw_vid)
        else:
            # Zone camera event — look up via track mapping
            visitor_id = visitor_mapper.get(cam, tid)
            if visitor_id is None and etype not in ("ZONE_ENTER","ZONE_EXIT","ZONE_DWELL"):
                visitor_id = raw_vid  # keep whatever we have

        # ── Staff flag ────────────────────────────────────────────────────────
        is_staff = staff_applicator.is_staff(cam, tid)

        # ── Session seq ───────────────────────────────────────────────────────
        seq_vid = visitor_id or f"_unknown_{cam}_{tid}"
        seq     = session_seq.next(seq_vid)

        # Save continuation seq for REENTRY (before we increment further)
        if etype in ("EXIT",) and visitor_id:
            reentry_resolver.set_continuation(visitor_id, seq)

        # ── POS billing tracking ──────────────────────────────────────────────
        if not is_staff:
            pos_correlator.on_event({**raw, "visitor_id": visitor_id, "is_staff": is_staff})

        # ── Build + validate final event ──────────────────────────────────────
        final, err = build_event(
            raw, visitor_id, is_staff, seq,
            zone_meta, billing_ids,
            converted=False,  # filled after POS correlation
        )

        if err:
            rejected_events.append({**raw, "_rejection_reason": err})
            continue

        # Dedup event_ids (shouldn't happen but guard it)
        if final["event_id"] in seen_ids:
            import uuid as _uuid
            final["event_id"] = str(_uuid.uuid4())
        seen_ids.add(final["event_id"])

        out_events.append(final)

    # ── POS correlation ───────────────────────────────────────────────────────
    converted_visitors = pos_correlator.correlate(pos_transactions, store_id)
    for ev in out_events:
        if ev.get("visitor_id") in converted_visitors:
            ev["converted"] = True

    # ── BILLING_QUEUE_ABANDON events ──────────────────────────────────────────
    abandon_events = pos_correlator.get_abandon_events(
        pos_transactions, store_id, session_seq.next
    )
    out_events.extend(abandon_events)

    # ── Re-sort and write ─────────────────────────────────────────────────────
    out_events.sort(key=lambda e: e.get("timestamp", ""))

    _write_jsonl(out_path, out_events)
    if rejected_events:
        _write_jsonl(reject_path, rejected_events)

    # ── Summary ───────────────────────────────────────────────────────────────
    from collections import Counter
    type_counts = Counter(e["event_type"] for e in out_events)
    print(f"\n✅  L6 complete")
    print(f"   {len(out_events)} events → {out_path}")
    if rejected_events:
        print(f"   {len(rejected_events)} rejected → {reject_path}")
    print(f"   Event types: {dict(type_counts)}")
    print(f"   Converted visitors: {len(converted_visitors)}")


if __name__ == "__main__":
    main()
