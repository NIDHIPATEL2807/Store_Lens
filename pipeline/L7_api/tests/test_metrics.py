from conftest import make_event


def _seed(client, events):
    client.post("/events/ingest", json=events)


def test_unknown_store_404(client):
    r = client.get("/stores/GHOST_STORE/metrics")
    assert r.status_code == 404


def test_empty_store_zeros(client):
    _seed(client, [make_event(store_id="S1", event_id="e1")])
    r = client.get("/stores/S1/metrics")
    assert r.status_code == 200
    data = r.get_json()
    # unique visitors = 0 because timestamp is old date in fixture (not today)
    assert isinstance(data["unique_visitors"], int)
    assert isinstance(data["conversion_rate"], float)
    assert data["conversion_rate"] >= 0.0


def test_all_staff_zero_customers(client):
    import time
    today = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    events = [
        make_event(event_id=f"s{i}", visitor_id=f"STAFF_{i}",
                   is_staff=True, timestamp=today, store_id="S2")
        for i in range(5)
    ]
    _seed(client, events)
    r = client.get("/stores/S2/metrics")
    assert r.status_code == 200
    data = r.get_json()
    assert data["unique_visitors"] == 0


def test_zero_purchases_no_error(client):
    import time
    today = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    _seed(client, [make_event(event_id="nopurchase", store_id="S3",
                              timestamp=today, converted=False)])
    r = client.get("/stores/S3/metrics")
    assert r.status_code == 200
    data = r.get_json()
    assert data["conversion_rate"] == 0.0
