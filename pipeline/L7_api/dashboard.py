"""
Purplle Store Analytics — Live Streamlit Dashboard
====================================================
Polls the FastAPI every N seconds and displays live store metrics.

Run:
    streamlit run dashboard.py

Requirements:
    pip install streamlit plotly requests
"""

import time
import requests
import streamlit as st
import plotly.graph_objects as go
import plotly.express as px

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Purplle Analytics",
    page_icon="💄",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.image("https://i.imgur.com/placeholder.png", width=40) if False else None
    st.title("💄 Purplle Analytics")
    st.markdown("---")

    api_url = st.text_input("API Base URL", value="http://localhost:8001")
    store_id = st.selectbox("Store", ["STORE_STORE_1", "STORE_STORE_2"])
    refresh_sec = st.slider("Auto-refresh (seconds)", 5, 60, 10)

    st.markdown("---")
    st.caption("Pipeline: L1→L2→L3→L4→L5→L6→L7")
    st.caption("Model: YOLOv8 + ByteTrack + OSNet")

    manual_refresh = st.button("🔄 Refresh Now")

# ── Fetch helpers ─────────────────────────────────────────────────────────────
def _get(endpoint: str) -> dict | None:
    try:
        r = requests.get(f"{api_url}{endpoint}", timeout=5)
        if r.status_code == 200:
            return r.json()
        return None
    except Exception:
        return None


@st.cache_data(ttl=refresh_sec)
def fetch_all(store: str, _tick: int):
    return {
        "metrics":   _get(f"/stores/{store}/metrics"),
        "funnel":    _get(f"/stores/{store}/funnel"),
        "heatmap":   _get(f"/stores/{store}/heatmap"),
        "anomalies": _get(f"/stores/{store}/anomalies"),
        "health":    _get("/health"),
    }


# ── Auto-refresh counter ──────────────────────────────────────────────────────
if "tick" not in st.session_state:
    st.session_state.tick = 0
if "last_refresh" not in st.session_state:
    st.session_state.last_refresh = time.time()

if manual_refresh or (time.time() - st.session_state.last_refresh >= refresh_sec):
    st.session_state.tick += 1
    st.session_state.last_refresh = time.time()

data = fetch_all(store_id, st.session_state.tick)

# ── Header ────────────────────────────────────────────────────────────────────
col_title, col_status = st.columns([4, 1])
with col_title:
    st.title(f"💄 {store_id.replace('STORE_', '').replace('_', ' ').title()}")
    health = data["health"]
    if health:
        db_ms  = health.get("db_latency_ms", "?")
        uptime = round(health.get("uptime_seconds", 0) / 60, 1)
        store_health = next(
            (s for s in health.get("stores", []) if s["store_id"] == store_id), None
        )
        stale = store_health.get("stale_feed", False) if store_health else False
        if stale:
            st.warning("⚠️ Feed is stale — no recent events")
        else:
            st.caption(f"🟢 API UP  |  DB {db_ms}ms  |  Uptime {uptime}m  |  "
                       f"Last refresh: {time.strftime('%H:%M:%S')}")
    else:
        st.error("🔴 Cannot reach API — is it running?")

with col_status:
    st.metric("Auto-refresh", f"{refresh_sec}s")

st.markdown("---")

# ── Key metrics row ───────────────────────────────────────────────────────────
metrics = data["metrics"]

if metrics:
    date = metrics.get("date", "—")
    st.subheader(f"📊 Key Metrics — {date}")

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("👥 Unique Visitors",    metrics.get("unique_visitors", 0))
    m2.metric("💰 Conversion Rate",    f"{metrics.get('conversion_rate', 0)*100:.1f}%")
    m3.metric("🧾 Queue Depth",        metrics.get("current_queue_depth", 0))
    m4.metric("🚪 Abandonment Rate",   f"{metrics.get('abandonment_rate', 0)*100:.1f}%")

    dwell_zones = metrics.get("avg_dwell_by_zone", [])
    if dwell_zones:
        top_zone = dwell_zones[0]
        m5.metric("⏱️ Top Dwell Zone",
                  top_zone["zone_id"],
                  f"{top_zone['avg_dwell_ms']/1000:.0f}s avg")
    else:
        m5.metric("⏱️ Top Dwell Zone", "—")
else:
    st.warning("No metrics data. Is the store ingested?")

st.markdown("---")

# ── Funnel + Heatmap side by side ─────────────────────────────────────────────
col_funnel, col_heat = st.columns(2)

with col_funnel:
    st.subheader("🔽 Conversion Funnel")
    funnel = data["funnel"]
    if funnel and funnel.get("stages"):
        stages  = funnel["stages"]
        labels  = [s["stage"].replace("_", " ").title() for s in stages]
        counts  = [s["count"] for s in stages]
        dropoffs = [s["dropoff_pct"] for s in stages]

        fig = go.Figure(go.Funnel(
            y = labels,
            x = counts,
            textinfo = "value+percent initial",
            marker = dict(color=["#6C3483", "#8E44AD", "#BB8FCE", "#D7BDE2"]),
            connector = dict(line=dict(color="#6C3483", width=2)),
        ))
        fig.update_layout(
            margin=dict(l=10, r=10, t=10, b=10),
            height=300,
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#333"),
        )
        st.plotly_chart(fig, use_container_width=True)

        # Dropoff table
        for i, s in enumerate(stages[1:], 1):
            if s["dropoff_pct"] > 0:
                st.caption(f"↘ {s['dropoff_pct']}% dropped before **{labels[i]}**")
    else:
        st.info("No funnel data yet.")

with col_heat:
    st.subheader("🗺️ Zone Heatmap")
    heatmap = data["heatmap"]
    if heatmap and heatmap.get("zones"):
        zones = heatmap["zones"][:10]  # top 10
        zone_names  = [z["zone_id"] for z in zones]
        zone_scores = [z["score"] for z in zones]
        zone_dwells = [round(z["avg_dwell_ms"] / 1000, 1) for z in zones]
        confidence  = zones[0].get("data_confidence", "OK") if zones else "OK"

        fig = px.bar(
            x=zone_scores, y=zone_names,
            orientation="h",
            labels={"x": "Hotness Score (0–100)", "y": "Zone"},
            color=zone_scores,
            color_continuous_scale=["#D7BDE2", "#6C3483"],
            text=[f"{s:.0f}" for s in zone_scores],
        )
        fig.update_layout(
            margin=dict(l=10, r=10, t=10, b=10),
            height=300,
            showlegend=False,
            coloraxis_showscale=False,
            paper_bgcolor="rgba(0,0,0,0)",
            yaxis=dict(autorange="reversed"),
        )
        st.plotly_chart(fig, use_container_width=True)

        if confidence == "LOW":
            st.caption("⚠️ Low confidence — fewer than 20 sessions")

        with st.expander("Dwell times by zone"):
            for z, d in zip(zone_names, zone_dwells):
                st.text(f"{z:25s}  {d}s avg dwell")
    else:
        st.info("No heatmap data yet.")

st.markdown("---")

# ── Anomalies ─────────────────────────────────────────────────────────────────
st.subheader("🚨 Active Anomalies")
anomalies_data = data["anomalies"]

if anomalies_data:
    anomalies = anomalies_data.get("anomalies", [])
    if anomalies:
        sev_colour = {"CRITICAL": "🔴", "WARN": "🟡", "INFO": "🔵"}
        for a in anomalies:
            icon = sev_colour.get(a["severity"], "⚪")
            with st.expander(f"{icon} {a['anomaly_type']}  —  {a['severity']}"):
                st.write(f"**Description:** {a['description']}")
                st.write(f"**Action:** {a['suggested_action']}")
                st.caption(f"Detected at: {a['detected_at']}")
    else:
        st.success("✅ No active anomalies")
else:
    st.info("Could not fetch anomalies.")

st.markdown("---")

# ── Health detail ─────────────────────────────────────────────────────────────
with st.expander("🏥 API Health Detail"):
    if health:
        hcol1, hcol2, hcol3 = st.columns(3)
        hcol1.metric("Status",       health.get("status", "?"))
        hcol2.metric("DB Latency",   f"{health.get('db_latency_ms', '?')}ms")
        hcol3.metric("API Version",  health.get("api_version", "?"))

        st.markdown("**Per-store feed status:**")
        for s in health.get("stores", []):
            icon = "🔴" if s.get("stale_feed") else "🟢"
            st.write(f"{icon} `{s['store_id']}` — last event: `{s.get('last_event_ts', 'never')}`  |  "
                     f"events last hour: `{s.get('events_last_hour', 0)}`")
    else:
        st.error("API unreachable")

# ── Auto-refresh via st.rerun ─────────────────────────────────────────────────
time.sleep(0.5)
if time.time() - st.session_state.last_refresh >= refresh_sec:
    st.rerun()
