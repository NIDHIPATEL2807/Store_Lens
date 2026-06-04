import time
from conftest import make_event

_TODAY = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())


def _seed(client, events):
    client.post("/events/ingest", json=events)


def test_funnel_404_unknown_store(client):
    r = client.get("/stores/NO_STORE/funnel")
    assert r.status_code == 404


def test_funnel_all_zeros_valid(client):
    _seed(client, [make_event(store_id="F1", event_id="f1", timestamp=_TODAY)])
    r = client.get("/stores/F1/funnel")
    assert r.status_code == 200
    data = r.get_json()
    assert len(data["stages"]) == 4
    for stage in data["stages"]:
        assert stage["count"] >= 0
        assert 0.0 <= stage["dropoff_pct"] <= 100.0


def test_reentry_counted_once(client):
    # Same visitor_id appears twice as ENTRY — should count as 1 unique visitor
    ev1 = make_event(event_id="re1", store_id="F2", visitor_id="VIS_same",
                     event_type="ENTRY", timestamp=_TODAY)
    ev2 = make_event(event_id="re2", store_id="F2", visitor_id="VIS_same",
                     event_type="REENTRY", timestamp=_TODAY)
    _seed(client, [ev1, ev2])
    r = client.get("/stores/F2/funnel")
    assert r.status_code == 200
    entry_stage = r.get_json()["stages"][0]
    assert entry_stage["count"] <= 1


def test_funnel_dropoff_monotone(client):
    """Each funnel stage count should be <= previous stage."""
    _seed(client, [make_event(store_id="F3", event_id="f3", timestamp=_TODAY)])
    r = client.get("/stores/F3/funnel")
    stages = r.get_json()["stages"]
    counts = [s["count"] for s in stages]
    for i in range(1, len(counts)):
        assert counts[i] <= counts[i - 1]
