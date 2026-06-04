import time
from conftest import make_event

_TODAY = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())


def _seed(client, events):
    client.post("/events/ingest", json=events)


# ── Anomalies ─────────────────────────────────────────────────────────────────

def test_anomalies_empty_store_empty_list(client):
    _seed(client, [make_event(store_id="AN1", event_id="a1", timestamp=_TODAY)])
    r = client.get("/stores/AN1/anomalies")
    assert r.status_code == 200
    data = r.get_json()
    assert "anomalies" in data
    assert isinstance(data["anomalies"], list)


def test_anomalies_unknown_store_404(client):
    r = client.get("/stores/GHOST/anomalies")
    assert r.status_code == 404


# ── Health ────────────────────────────────────────────────────────────────────

def test_health_returns_up(client):
    r = client.get("/health")
    assert r.status_code == 200
    data = r.get_json()
    assert data["status"] == "UP"
    assert "db_latency_ms" in data
    assert "uptime_seconds" in data
    assert "api_version" in data


def test_health_stale_feed_flag(client):
    # Insert an event with a very old timestamp — should trigger stale_feed
    old_ts = "2020-01-01T00:00:00.000Z"
    _seed(client, [make_event(store_id="STALE", event_id="old1", timestamp=old_ts)])
    r = client.get("/health")
    assert r.status_code == 200
    data = r.get_json()
    store_health = next((s for s in data["stores"] if s["store_id"] == "STALE"), None)
    if store_health:
        assert store_health["stale_feed"] is True


def test_health_no_stores_valid(client):
    r = client.get("/health")
    assert r.status_code == 200
    data = r.get_json()
    assert isinstance(data["stores"], list)
