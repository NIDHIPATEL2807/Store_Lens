import time
from conftest import make_event

_TODAY = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())


def test_funnel_404_unknown_store(client):
    r = client.get("/stores/NO_STORE/funnel")
    assert r.status_code == 404


def test_funnel_all_zeros_valid(client):
    client.post("/events/ingest", json=[make_event(store_id="F1", event_id="f1", timestamp=_TODAY)])
    r = client.get("/stores/F1/funnel")
    assert r.status_code == 200
    data = r.json()
    assert len(data["stages"]) == 4
    for stage in data["stages"]:
        assert stage["count"] >= 0
        assert 0.0 <= stage["dropoff_pct"] <= 100.0


def test_reentry_counted_once(client):
    ev1 = make_event(event_id="re1", store_id="F2", visitor_id="VIS_same",
                     event_type="ENTRY", timestamp=_TODAY)
    ev2 = make_event(event_id="re2", store_id="F2", visitor_id="VIS_same",
                     event_type="REENTRY", timestamp=_TODAY)
    client.post("/events/ingest", json=[ev1, ev2])
    r = client.get("/stores/F2/funnel")
    assert r.status_code == 200
    assert r.json()["stages"][0]["count"] <= 1


def test_funnel_dropoff_monotone(client):
    client.post("/events/ingest", json=[make_event(store_id="F3", event_id="f3", timestamp=_TODAY)])
    r = client.get("/stores/F3/funnel")
    counts = [s["count"] for s in r.json()["stages"]]
    for i in range(1, len(counts)):
        assert counts[i] <= counts[i - 1]
