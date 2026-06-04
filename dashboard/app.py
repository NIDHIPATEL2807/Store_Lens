"""
Purplle Store Analytics — Live Streamlit Dashboard
Deploy to Streamlit Cloud, set API_URL secret = your Render URL.
Local:  streamlit run app.py
"""

import os, time
import requests
import streamlit as st
import plotly.graph_objects as go
import plotly.express as px

_DEFAULT_API = (
    st.secrets.get("API_URL")
    if hasattr(st, "secrets") and "API_URL" in (st.secrets or {})
    else os.getenv("API_URL", "http://localhost:8001")
)

st.set_page_config(page_title="Purplle Analytics", page_icon="💄", layout="wide")

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("💄 Purplle Analytics")
    st.markdown("---")
    api_url     = st.text_input("API Base URL", value=_DEFAULT_API)
    store_id    = st.selectbox("Store", ["STORE_STORE_1", "STORE_STORE_2"])
    refresh_sec = st.slider("Auto-refresh (s)", 5, 60, 10)
    st.markdown("---")
    st.caption("YOLOv8 + ByteTrack + OSNet")
    st.caption("FastAPI + SQLite")
    manual = st.button("🔄 Refresh Now")

# ── Fetch ─────────────────────────────────────────────────────────────────────
def _get(path):
    try:
        r = requests.get(f"{api_url}{path}", timeout=8)
        return r.json() if r.status_code == 200 else None
    except Exception:
        return None

@st.cache_data(ttl=refresh_sec)
def fetch_all(store, _tick):
    return {
        "metrics":   _get(f"/stores/{store}/metrics"),
        "funnel":    _get(f"/stores/{store}/funnel"),
        "heatmap":   _get(f"/stores/{store}/heatmap"),
        "anomalies": _get(f"/stores/{store}/anomalies"),
        "health":    _get("/health"),
    }

if "tick"         not in st.session_state: st.session_state.tick = 0
if "last_refresh" not in st.session_state: st.session_state.last_refresh = time.time()

if manual or (time.time() - st.session_state.last_refresh >= refresh_sec):
    st.session_state.tick += 1
    st.session_state.last_refresh = time.time()

data = fetch_all(store_id, st.session_state.tick)

# ── Header ────────────────────────────────────────────────────────────────────
st.title(f"💄 {store_id.replace('STORE_','').replace('_',' ').title()}")
health = data["health"]
if health:
    db_ms  = health.get("db_latency_ms", "?")
    uptime = round(health.get("uptime_seconds", 0) / 60, 1)
    sh     = next((s for s in health.get("stores",[]) if s["store_id"]==store_id), None)
    if sh and sh.get("stale_feed"):
        st.warning("⚠️ Stale feed — no recent events")
    else:
        st.caption(f"🟢 API UP  |  DB {db_ms}ms  |  Uptime {uptime}m  |  {time.strftime('%H:%M:%S')}")
else:
    st.error("🔴 Cannot reach API — is it running?")

st.divider()

# ── Metrics ───────────────────────────────────────────────────────────────────
m = data["metrics"]
if m:
    st.subheader(f"📊 Key Metrics — {m.get('date','—')}")
    c1,c2,c3,c4,c5 = st.columns(5)
    c1.metric("👥 Visitors",       m.get("unique_visitors", 0))
    c2.metric("💰 Conversion",     f"{m.get('conversion_rate',0)*100:.1f}%")
    c3.metric("🧾 Queue Depth",    m.get("current_queue_depth", 0))
    c4.metric("🚪 Abandonment",    f"{m.get('abandonment_rate',0)*100:.1f}%")
    dz = m.get("avg_dwell_by_zone", [])
    if dz:
        c5.metric("⏱️ Top Zone", dz[0]["zone_id"], f"{dz[0]['avg_dwell_ms']/1000:.0f}s")
    else:
        c5.metric("⏱️ Top Zone", "—")
else:
    st.warning("No metrics — store not ingested yet.")

st.divider()

# ── Funnel + Heatmap ──────────────────────────────────────────────────────────
cf, ch = st.columns(2)

with cf:
    st.subheader("🔽 Conversion Funnel")
    funnel = data["funnel"]
    if funnel and funnel.get("stages"):
        stgs   = funnel["stages"]
        labels = [s["stage"].replace("_"," ").title() for s in stgs]
        counts = [s["count"] for s in stgs]
        fig = go.Figure(go.Funnel(
            y=labels, x=counts, textinfo="value+percent initial",
            marker=dict(color=["#6C3483","#8E44AD","#BB8FCE","#D7BDE2"]),
            connector=dict(line=dict(color="#6C3483", width=2)),
        ))
        fig.update_layout(margin=dict(l=0,r=0,t=0,b=0), height=280,
                          paper_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig, use_container_width=True)
        for i, s in enumerate(stgs[1:], 1):
            if s["dropoff_pct"] > 0:
                st.caption(f"↘ {s['dropoff_pct']}% lost before **{labels[i]}**")
    else:
        st.info("No funnel data.")

with ch:
    st.subheader("🗺️ Zone Heatmap")
    heatmap = data["heatmap"]
    if heatmap and heatmap.get("zones"):
        zs     = heatmap["zones"][:10]
        names  = [z["zone_id"] for z in zs]
        scores = [z["score"] for z in zs]
        dwells = [round(z["avg_dwell_ms"]/1000,1) for z in zs]
        fig = px.bar(x=scores, y=names, orientation="h",
                     color=scores, color_continuous_scale=["#D7BDE2","#6C3483"],
                     text=[f"{s:.0f}" for s in scores],
                     labels={"x":"Score (0–100)","y":"Zone"})
        fig.update_layout(margin=dict(l=0,r=0,t=0,b=0), height=280,
                          showlegend=False, coloraxis_showscale=False,
                          paper_bgcolor="rgba(0,0,0,0)",
                          yaxis=dict(autorange="reversed"))
        st.plotly_chart(fig, use_container_width=True)
        if zs[0].get("data_confidence") == "LOW":
            st.caption("⚠️ Low confidence (<20 sessions)")
        with st.expander("Dwell times"):
            for n, d in zip(names, dwells):
                st.text(f"{n:25s}  {d}s")
    else:
        st.info("No heatmap data.")

st.divider()

# ── Anomalies ─────────────────────────────────────────────────────────────────
st.subheader("🚨 Active Anomalies")
SEV = {"CRITICAL":"🔴","WARN":"🟡","INFO":"🔵"}
ad  = data["anomalies"]
if ad:
    anoms = ad.get("anomalies", [])
    if anoms:
        for a in anoms:
            with st.expander(f"{SEV.get(a['severity'],'⚪')} {a['anomaly_type']} — {a['severity']}"):
                st.write(f"**{a['description']}**")
                st.write(f"Action: {a['suggested_action']}")
                st.caption(a['detected_at'])
    else:
        st.success("✅ No active anomalies")
else:
    st.info("No anomaly data.")

st.divider()

# ── Health ────────────────────────────────────────────────────────────────────
with st.expander("🏥 API Health"):
    if health:
        h1,h2,h3 = st.columns(3)
        h1.metric("Status",      health.get("status","?"))
        h2.metric("DB Latency",  f"{health.get('db_latency_ms','?')}ms")
        h3.metric("API Version", health.get("api_version","?"))
        for s in health.get("stores",[]):
            icon = "🔴" if s.get("stale_feed") else "🟢"
            st.write(f"{icon} `{s['store_id']}` — last: `{s.get('last_event_ts','never')}` | /hr: `{s.get('events_last_hour',0)}`")
    else:
        st.error("Unreachable")

# ── Auto rerun ────────────────────────────────────────────────────────────────
time.sleep(0.5)
if time.time() - st.session_state.last_refresh >= refresh_sec:
    st.rerun()
