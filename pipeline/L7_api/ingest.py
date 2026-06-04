import json, requests

for f in [
    '../L6_emit/output/store1/events.jsonl',
    '../L6_emit/output/store2/events.jsonl',
]:
    try:
        batch = [json.loads(l) for l in open(f, encoding='utf-8') if l.strip()]
        print(f"  {f}  →  {len(batch)} events")
        for i in range(0, len(batch), 500):
            r = requests.post('http://localhost:8001/events/ingest', json=batch[i:i+500])
            print(f"    chunk {i//500+1}: {r.json()}")
    except Exception as e:
        print(f"  ERROR {f}: {e}")
