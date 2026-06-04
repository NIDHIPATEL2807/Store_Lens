from conftest import make_event


def test_single_event_accepted(client):
    r = client.post("/events/ingest", json=[make_event()])
    assert r.status_code == 200
    data = r.json()
    assert data["accepted"] == 1
    assert data["rejected"] == 0


def test_idempotent_double_ingest(client):
    ev = make_event(event_id="dup-001")
    client.post("/events/ingest", json=[ev])
    r = client.post("/events/ingest", json=[ev])
    assert r.status_code == 200
    data = r.json()
    assert data["accepted"] == 0
    assert data["rejected"] == 0


def test_partial_batch_207(client):
    good = [make_event(event_id=f"g-{i}", visitor_id=f"VIS_{i:03d}") for i in range(5)]
    bad  = [{"not": "an event"} for _ in range(2)]
    r = client.post("/events/ingest", json=good + bad)
    assert r.status_code == 207
    data = r.json()
    assert data["accepted"] == 5
    assert data["rejected"] == 2
    assert len(data["rejections"]) == 2


def test_batch_too_large_400(client):
    events = [make_event(event_id=f"e-{i}") for i in range(501)]
    r = client.post("/events/ingest", json=events)
    assert r.status_code == 400


def test_single_object_accepted(client):
    ev = make_event(event_id="obj-001")
    r = client.post("/events/ingest", json=ev)
    assert r.status_code == 200
    assert r.json()["accepted"] == 1


def test_invalid_json_400(client):
    r = client.post(
        "/events/ingest",
        content=b"not-json",
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 400
