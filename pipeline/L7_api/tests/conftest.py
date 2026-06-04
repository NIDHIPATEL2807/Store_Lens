"""Shared fixtures. Uses a fresh in-memory SQLite DB per test."""

import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

os.environ["DATABASE_URL"] = ":memory:"

import database
from main import app
from fastapi.testclient import TestClient


def _patch_db(monkeypatch):
    import sqlite3
    from contextlib import contextmanager

    _conn = sqlite3.connect(":memory:")
    _conn.row_factory = sqlite3.Row
    _conn.executescript(database._DDL)
    _conn.commit()

    @contextmanager
    def _get_db():
        try:
            yield _conn
            _conn.commit()
        except Exception:
            _conn.rollback()
            raise

    monkeypatch.setattr(database, "get_db", _get_db)
    monkeypatch.setattr(database, "ping", lambda: 1.0)
    return _conn


@pytest.fixture
def client(monkeypatch):
    _patch_db(monkeypatch)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db_conn(monkeypatch):
    return _patch_db(monkeypatch)


def make_event(**overrides) -> dict:
    base = {
        "event_id":   "evt-001",
        "store_id":   "STORE_1",
        "camera_id":  "CAM_ENTRY_01",
        "visitor_id": "VIS_abc123",
        "event_type": "ENTRY",
        "timestamp":  "2026-03-08T10:00:00.000Z",
        "zone_id":    None,
        "dwell_ms":   0,
        "is_staff":   False,
        "confidence": 0.85,
        "converted":  False,
        "metadata": {
            "queue_depth": None, "sku_zone": None,
            "session_seq": 1, "group_id": None,
            "group_size": None, "track_id": 1,
        },
    }
    base.update(overrides)
    return base
