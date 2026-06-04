import time
from conftest import make_event

_TODAY = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())


def test_unknown_store_404(client):
    r = client.get("/stores/GHOST_STORE/metrics")
    assert r.status_code == 404


def test_empty_store_zeros(client):
    client.post("/events/ingest", json=[make_event(store_id="S1", event_id="e1")])
    r = client.get("/stores/S1/metrics")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data["unique_visitors"], int)
    assert isinstance(data["conversion_rate"], float)
    assert data["conversion_rate"] >= 0.0


def test_all_staff_zero_customers(client):
    events = [
        make_event(event_id=f"s{i}", visitor_id=f"STAFF_{i}",
                   is_staff=True, timestamp=_TODAY, store_id="S2")
        for i in range(5)
    ]
    client.post("/events/ingest", json=events)
    r = client.get("/stores/S2/metrics")
    assert r.status_code == 200
    assert r.json()["unique_visitors"] == 0


def test_zero_purchases_no_error(client):
    client.post("/events/ingest", json=[
        make_event(event_id="nopurchase", store_id="S3", timestamp=_TODAY, converted=False)
    ])
    r = client.get("/stores/S3/metrics")
    assert r.status_code == 200
    assert r.json()["conversion_rate"] == 0.0
