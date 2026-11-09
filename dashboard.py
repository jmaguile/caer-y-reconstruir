"""Caer y Reconstruir — Market Investigator (Bloomberg-terminal Streamlit surface).

Live trading-terminal feel for the autonomous loop:
  - Hero strip across the top reads memory/ticks.jsonl: live loop pulse, last
    contradiction score, last case-file time, grid live/baseline indicator.
  - Sidebar lists trending Polymarket markets with color-coded odds + an
    inline unicode sparkline of their 7-day trajectory.
  - Main panel for the selected market:
      * live ticker fragment that auto-refreshes every 3 seconds, reading
        the most recent book + last-trade from the Polymarket CLOB websocket
        (fetchers/polymarket_ws.py — thread-owned, cached as a singleton).
      * lifetime price chart with rolling-mean band and anomaly markers.
      * El Inspector panel: top-3 |z|>1.8 anomalies on the lifetime curve,
        each annotated with a Nemotron-generated one-sentence catalyst
        explanation built from GDELT headlines for that ±3-day window.
      * Price-level history rows (kept from previous dashboard).
      * Loop activity panel: rolling tail of the loop's tick log so judges
        see run.py is alive even when no market is selected.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# One-time terminal banner so the clickable URL is the first thing the user
# sees — independent of Streamlit's own three-URL announcement.
if not os.environ.get("_CYR_BANNER_DISPLAYED"):
    os.environ["_CYR_BANNER_DISPLAYED"] = "1"
    _url = "http://100.119.28.20:8501"
    _banner = (
        "\n" + "=" * 60
        + "\n  Caer y Reconstruir — Market Investigator"
        + "\n"
        + "\n  Open this URL in your laptop browser:"
        + f"\n\n      {_url}"
        + "\n\n" + "=" * 60 + "\n"
    )
    print(_banner, file=sys.stderr, flush=True)

import altair as alt
import pandas as pd
import streamlit as st

from fetchers import polymarket, gdelt
from fetchers.polymarket_ws import PolymarketWS
from event_matcher import _load_timeline, _nearest_timeline_event
from agents import inspector, narrator
import dashboard_theme as tt

HERE = Path(__file__).resolve().parent
PRED_LOG = HERE / "memory" / "predictions.jsonl"
TICK_LOG = HERE / "memory" / "ticks.jsonl"

# Old fixed bands (5¢…85¢) only made sense for markets that swing across the
# full range. For a market trading 0.005–0.020 (e.g. Vivek 2028) every band
# showed "never traded". We now derive bands from the market's own lifetime
# min/max so the panel is always informative.
BAND_QUANTILES = [0.10, 0.25, 0.40, 0.55, 0.70, 0.85, 0.95]
MIN_RANGE_FOR_BANDS = 0.01   # below this, the panel isn't useful
BAND_TOLERANCE_FRAC = 0.08   # tolerance as 8% of lifetime range


# ----------------------- Data accessors -----------------------

@st.cache_resource(show_spinner=False)
def get_ws_client() -> PolymarketWS:
    """Singleton ws client — survives Streamlit reruns via cache_resource."""
    return PolymarketWS()


@st.cache_data(ttl=300, show_spinner=False)
def get_markets():
    return polymarket.fetch()


@st.cache_data(ttl=3600, show_spinner=False)
def get_lifetime(token_id: str):
    return polymarket.fetch_lifetime_history(token_id)


@st.cache_data(ttl=60, show_spinner=False)
def load_jsonl(path_str: str) -> list[dict]:
    path = Path(path_str)
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


@st.cache_data(ttl=900, show_spinner="El Inspector hunting anomalies…")
def get_inspector_analysis(token_id: str, history_len: int,
                           question: str) -> dict:
    """Cached per (token_id, history_len) — same dataset → same result.
    Returns DataFrame + anomaly list (DataFrame is pickleable; fine for cache).
    """
    history = get_lifetime(token_id)
    return inspector.analyze(history, question, k=3, interpret=True)


@st.cache_data(ttl=900, show_spinner="El Narrador briefing the market…")
def get_market_briefing(token_id: str, history_len: int, question: str,
                        current_price: float) -> str:
    history = get_lifetime(token_id)
    return narrator.summarize_market(question, history, current_price)


@st.cache_data(ttl=900, show_spinner="El Narrador narrating the price journey…")
def get_price_journey(token_id: str, history_len: int, question: str) -> str:
    history = get_lifetime(token_id)
    return narrator.summarize_price_journey(question, history)


@st.cache_data(ttl=600, show_spinner="El Narrador reading the loop…")
def get_loop_briefing(tick_count: int, last_logged_at: str) -> str:
    # Cache key uses tick count + last timestamp so we re-narrate when
    # new ticks land, but reuse within a single state.
    ticks = load_jsonl(str(TICK_LOG))
    return narrator.summarize_loop(ticks)


# ----------------------- Small helpers -----------------------

def adaptive_bands(history: list) -> list[tuple[float, str, float]]:
    """Returns [(band_center, label, tolerance)] derived from the market's
    actual lifetime min/max. Returns [] if range is too narrow to be useful."""
    if not history:
        return []
    prices = [p for _, p in history]
    lo, hi = min(prices), max(prices)
    rng = hi - lo
    if rng < MIN_RANGE_FOR_BANDS:
        return []
    tol = max(0.002, rng * BAND_TOLERANCE_FRAC)
    bands = []
    for q in BAND_QUANTILES:
        center = lo + q * rng
        # Format label as ¢ when ≥ 0.10, three decimals when smaller
        label = f"{center*100:.0f}¢" if center >= 0.10 else f"{center:.3f}"
        bands.append((round(center, 4), label, round(tol, 4)))
    return bands


def find_last_in_band(series, band_center, tolerance):
    if not series:
        return None
    for ts, price in reversed(series):
        if abs(price - band_center) <= tolerance:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
    return None


def news_for_date(target_dt, question):
    event = _nearest_timeline_event(target_dt)
    if event:
        return ("timeline",
                f"📚 **{event['date']} — {event['headline']}**  \n"
                f"{event.get('context', '')}  \n"
                f"_tags: {', '.join(event.get('tags', []))}_")
    if os.getenv("ENABLE_GDELT_ECHO_FALLBACK", "").lower() in ("1", "true", "yes"):
        keywords = " ".join(w for w in question.split() if len(w) > 3)[:120] or "energy"
        res = gdelt.fetch_around_date(target_dt, keywords, window_days=3, limit=1)
        if res and res.get("articles"):
            top = res["articles"][0]
            return ("gdelt",
                    f"📰 **~{target_dt.date()} — {top.get('title', '')[:200]}**  \n"
                    f"_source: {top.get('domain', '?')} (GDELT live lookup)_")
    return ("none", f"_no curated event within ±3 days of {target_dt.date()}_")


# ----------------------- Rendering blocks -----------------------

def render_hero_strip(predictions: list[dict], ticks: list[dict]):
    """Top status row reading run.py's persistent state."""
    last_tick = ticks[-1] if ticks else {}
    last_score = last_tick.get("contradiction_score", "—")
    last_score_s = f"{last_score:.2f}" if isinstance(last_score, (int, float)) else "—"
    score_color = tt.RED if isinstance(last_score, (int, float)) and last_score >= 6 \
        else tt.AMBER if isinstance(last_score, (int, float)) and last_score >= 3 \
        else tt.GREEN

    case_files = [p for p in predictions if p.get("case_file")]
    last_case = case_files[-1].get("logged_at", "—")[:19] if case_files else "—"

    grid_src = last_tick.get("cenace_source", "—")
    grid_label = "LIVE" if "electricitymaps_live" in str(grid_src) else \
                 "BASELINE" if "baseline" in str(grid_src) else "—"
    grid_color = tt.GREEN if grid_label == "LIVE" else tt.AMBER

    fossil = last_tick.get("fossil_pct", "—")
    fossil_s = f"{fossil}%" if isinstance(fossil, (int, float)) else "—"

    # Loop heartbeat: how long since last tick?
    if ticks:
        last_at = ticks[-1].get("logged_at", "")
        try:
            dt = datetime.fromisoformat(last_at)
            age = (datetime.utcnow() - dt).total_seconds()
            alive = age < 180  # 3 minutes
            beat = f"{int(age)}s ago" if age < 600 else f"{int(age/60)}m ago"
        except Exception:
            alive, beat = False, "—"
    else:
        alive, beat = False, "—"

    cols = st.columns([2, 1.4, 1.4, 1.4, 1.4])
    with cols[0]:
        tooltip = ("The autonomous loop in run.py: every 60s it pulls fresh "
                   "data (Polymarket, GDELT news, CENACE grid, macro), runs "
                   "the 5-agent chain (Observador → Cronista → Analista → "
                   "Testigo → optionally Relator), and appends to "
                   "memory/ticks.jsonl. This timer shows seconds since the "
                   "last logged tick. Green dot = alive (<3min). Red = stalled.")
        st.markdown(
            f"<div class='tt-panel' style='border-color:{tt.AMBER}' "
            f"title='{tooltip}'>"
            f"<div class='tt-label'>caer y reconstruir · market investigator</div>"
            f"<div class='tt-value' style='font-size:1.0rem'>"
            f"{tt.live_dot(alive, 'loop ' + beat)}</div>"
            f"<div class='tt-sub'>nemotron nano 9b v2 · DGX Spark · hover for details</div>"
            f"</div>", unsafe_allow_html=True)
    cols[1].markdown(tt.hero_tile("ticks logged", str(len(ticks)),
                                  f"{len(predictions)} predictions"),
                     unsafe_allow_html=True)
    cols[2].markdown(tt.hero_tile("last contradiction", last_score_s,
                                  "0–10 scale", color=score_color),
                     unsafe_allow_html=True)
    cols[3].markdown(tt.hero_tile("last case file", last_case[-8:] or "—",
                                  last_case[:10] if last_case != "—" else ""),
                     unsafe_allow_html=True)
    cols[4].markdown(tt.hero_tile("grid", grid_label, f"fossil {fossil_s}",
                                  color=grid_color),
                     unsafe_allow_html=True)


def _passes_quality_gate(m: dict, min_vol: float = 1_000_000) -> bool:
    """Filter out markets that won't have informative panels: low volume,
    or odds so near 0/1 that the price-band and anomaly panels go empty."""
    if m.get("volume", 0) < min_vol:
        return False
    odds = m.get("yes_odds", 0) or 0
    if odds < 0.05 or odds > 0.95:
        return False
    h7 = (m.get("history_7d") or {})
    # If we already have 7d range data, require non-trivial movement
    if "range_7d" in h7 and h7["range_7d"] < 0.02:
        return False
    return True


_HEALTH_ICON = {"good": "✓", "partial": "◐", "empty": "✗", "unknown": "·"}
_HEALTH_DESC = {
    "good": "demo-ready: most pivots got real GDELT catalysts",
    "partial": "some pivots have catalysts, some don't",
    "empty": "no pivots returned usable catalysts",
    "unknown": "not analyzed yet — click to investigate",
}


def render_sidebar(markets: dict) -> str | None:
    with st.sidebar:
        st.markdown("### 📈 trending markets")
        quality_only = st.toggle(
            "info-rich only",
            value=True,
            help=("On (default): hide markets with <$1M volume, odds outside "
                  "0.05–0.95, or 7d range <0.02 — these surface empty panels. "
                  "Off: show every relevance-matched market.")
        )
        hide_empty_markets = st.toggle(
            "hide known-empty markets",
            value=True,
            help=("On: once a market's pivot analysis returns no usable "
                  "catalysts (✗), hide it from this list — keeps the demo "
                  "tight. Off: show all, with ✗ markers on known-empty ones.")
        )
        # Empty pivot cards are ALWAYS hidden — no toggle. A pivot without a
        # real catalyst is noise on stage; we never show it.
        st.session_state["hide_empty_pivots"] = True

        all_slugs = sorted(markets.keys(),
                           key=lambda s: markets[s].get("volume", 0),
                           reverse=True)
        if quality_only:
            sorted_slugs = [s for s in all_slugs
                            if _passes_quality_gate(markets[s])]
        else:
            sorted_slugs = all_slugs

        # Apply the known-empty filter from session_state health records
        health_map = st.session_state.get("market_health", {})
        if hide_empty_markets:
            sorted_slugs = [s for s in sorted_slugs
                            if (health_map.get(s) or {}).get("label") != "empty"]

        hidden = len(all_slugs) - len(sorted_slugs)
        caption_bits = [f"{len(sorted_slugs)} shown",
                        "sorted by 24h volume"]
        if hidden > 0:
            caption_bits.append(f"{hidden} hidden")
        st.caption(" · ".join(caption_bits))

        if st.button("Refresh feed"):
            get_markets.clear()
            get_lifetime.clear()
            load_jsonl.clear()
            get_inspector_analysis.clear()
            st.rerun()

        if not sorted_slugs:
            st.warning("No markets pass the quality gate. Toggle "
                       "'info-rich only' off to see everything.")
            return None

        # Compact sidebar cards: question on line 1 (truncated, with health
        # icon prefix once known), then `odds sparkline vol arrow` on line 2,
        # then matched keywords on line 3 in dim font.
        labels = {}
        for slug in sorted_slugs:
            m = markets[slug]
            spark = ""
            h7 = m.get("history_7d") or {}
            pts = h7.get("points") or []
            if pts:
                spark = tt.sparkline_unicode([p for _, p in pts], width=12)
            q = (m.get("question") or slug)[:40]
            if len(m.get("question") or slug) > 40:
                q += "…"
            vol = tt.fmt_volume(m.get("volume", 0))
            odds = m.get("yes_odds", 0)
            arrow = ""
            ch = h7.get("change_7d", 0) if h7 else 0
            if ch > 0.01:
                arrow = f" ▲{ch:+.2f}"
            elif ch < -0.01:
                arrow = f" ▼{ch:+.2f}"
            kws = m.get("match_keywords") or []
            kw_line = ("match: " + ", ".join(kws)) if kws else ""
            health = (health_map.get(slug) or {}).get("label", "unknown")
            icon = _HEALTH_ICON.get(health, "·")
            labels[slug] = (f"{icon} {q}\n  {odds:.2f} {spark} {vol}{arrow}\n"
                            f"  {kw_line}")

        selected = st.radio(
            "Investigate",
            sorted_slugs,
            format_func=lambda s: labels[s],
            label_visibility="collapsed",
        )

        st.markdown("---")
        ws = get_ws_client()
        status = ws.status()
        st.markdown(
            tt.live_dot(status["connected"],
                        f"ws {'live' if status['connected'] else 'reconnecting…'}"
                        f" · subs {len(status['subscribed'])}"
                        + (f" · reconnects {status['reconnects']-1}"
                           if status['reconnects'] > 1 else "")),
            unsafe_allow_html=True)
        if status["last_msg_ago"] is not None:
            st.caption(f"last ws msg {status['last_msg_ago']:.0f}s ago")

        return selected


@st.fragment(run_every=3)
def render_live_ticker(token_id: str, fallback_odds: float):
    """Auto-refreshing block that reads from the ws client every 3 seconds.
    LIVE is the canonical price when the ws feed has produced any data; the
    REST snapshot is shown only as a delta below, so judges see one number,
    not two competing ones."""
    ws = get_ws_client()
    ws.ensure_subscribed([token_id])
    latest = ws.latest(token_id) or {}
    ticks = ws.tick_series(token_id)
    trades = ws.recent_trades(token_id, n=6)

    bid = latest.get("best_bid", 0) or 0
    ask = latest.get("best_ask", 0) or 0
    spread = (ask - bid) if (bid > 0 and ask > 0) else 0
    age = (time.time() - latest["ts"]) if latest.get("ts") else None

    # Headline price: prefer last trade, then mid, then nothing (NOT the REST
    # fallback — we want the user to clearly see when ws hasn't connected yet)
    ws_price = latest.get("last_price") or latest.get("mid")
    if ws_price:
        headline_value = f"{ws_price:.4f}"
        delta = ws_price - fallback_odds
        sub = (f"{age:.0f}s ago · REST was {fallback_odds:.4f} "
               f"({'+' if delta >= 0 else ''}{delta:+.4f})")
    else:
        headline_value = "—"
        sub = f"awaiting first ws event · REST snapshot {fallback_odds:.4f}"

    cols = st.columns([1.4, 1, 1, 1, 2])
    cols[0].markdown(tt.metric_block("LIVE PRICE", headline_value, sub),
                     unsafe_allow_html=True)
    cols[1].markdown(tt.metric_block(
        "BEST BID", f"{bid:.4f}" if bid else "—",
        value_color=tt.GREEN),
        unsafe_allow_html=True)
    cols[2].markdown(tt.metric_block(
        "BEST ASK", f"{ask:.4f}" if ask else "—",
        value_color=tt.RED),
        unsafe_allow_html=True)
    cols[3].markdown(tt.metric_block(
        "SPREAD", f"{spread:.4f}" if spread else "—"),
        unsafe_allow_html=True)
    with cols[4]:
        st.markdown("<div class='tt-label'>Recent trades</div>",
                    unsafe_allow_html=True)
        if not trades:
            st.markdown("<div class='tt-sub'>none yet — live trades will stream in here</div>",
                        unsafe_allow_html=True)
        else:
            rows = []
            for ts, price, size, side in reversed(trades):
                clock = datetime.fromtimestamp(ts).strftime("%H:%M:%S")
                color = tt.GREEN if side == "buy" else tt.RED if side == "sell" else tt.DIM
                rows.append(
                    f"<div class='tt-tick-row'>"
                    f"<span style='color:{tt.DIM}'>{clock}</span>"
                    f"<span style='color:{color}'>{side or '?'}</span>"
                    f"<span style='color:{tt.AMBER}'>{price:.3f}</span>"
                    f"<span>{size:.0f}</span>"
                    f"</div>"
                )
            st.markdown("".join(rows), unsafe_allow_html=True)

    # Only render the in-session mini-chart once there are enough ticks to
    # be meaningful. Otherwise the empty chart eats vertical space and
    # creates the "top of chart cut off" feeling above the lifetime panel.
    if ticks and len(ticks) >= 2:
        df = pd.DataFrame(ticks, columns=["ts", "mid"])
        df["t"] = pd.to_datetime(df["ts"], unit="s")
        df = df.set_index("t")[["mid"]]
        st.line_chart(df, height=120)
    elif not ticks:
        st.caption("ws connected — waiting for first book/trade event…")


def render_lifetime_with_anomalies(history, analysis):
    """Lifetime price chart with rolling-mean band, ALL detected pivots as
    colored dots (HH/LH/HL/LL), and a y-axis domain padded so peaks/troughs
    don't crowd the chart edges."""
    df = pd.DataFrame(history, columns=["ts", "price"])
    df["date"] = pd.to_datetime(df["ts"], unit="s", utc=True)
    df = df.sort_values("date").reset_index(drop=True)

    bands = analysis.get("bands")
    pivots = analysis.get("pivots", []) or analysis.get("anomalies", [])

    # Pad y-axis so the peak/trough have headroom (10% of range, min 0.02)
    lo, hi = float(df["price"].min()), float(df["price"].max())
    pad = max(0.02, (hi - lo) * 0.10)
    y_domain = [max(0.0, lo - pad), min(1.0, hi + pad)]

    layers = []
    if bands is not None and not bands.empty:
        band_df = bands.reset_index().rename(columns={"date": "date"})
        band_df["date"] = pd.to_datetime(band_df["date"], utc=True)
        band_area = alt.Chart(band_df).mark_area(
            opacity=0.12, color=tt.CYAN
        ).encode(
            x=alt.X("date:T", axis=alt.Axis(title=None, labelColor=tt.DIM,
                                            gridColor=tt.BORDER)),
            y=alt.Y("lower:Q", scale=alt.Scale(domain=y_domain),
                    axis=alt.Axis(title=None, labelColor=tt.DIM,
                                  gridColor=tt.BORDER)),
            y2=alt.Y2("upper:Q"),
        )
        rolling = alt.Chart(band_df).mark_line(
            color=tt.CYAN, strokeWidth=1.2, strokeDash=[4, 3]
        ).encode(x="date:T",
                 y=alt.Y("rolling_mean:Q", scale=alt.Scale(domain=y_domain)))
        layers += [band_area, rolling]

    price_line = alt.Chart(df).mark_line(color=tt.AMBER, strokeWidth=1.8).encode(
        x=alt.X("date:T", axis=alt.Axis(title=None, labelColor=tt.DIM,
                                        gridColor=tt.BORDER)),
        y=alt.Y("price:Q", scale=alt.Scale(domain=y_domain),
                axis=alt.Axis(title="YES odds", labelColor=tt.DIM,
                              gridColor=tt.BORDER)),
        tooltip=[alt.Tooltip("date:T"), alt.Tooltip("price:Q", format=".4f")],
    )
    layers.append(price_line)

    if pivots:
        # Color by pivot label. Filled (size 140) for interpreted, smaller
        # outlined (size 60) for detected-but-not-interpreted.
        adf = pd.DataFrame([{
            "date": pd.to_datetime(p["date"], utc=True),
            "price": p["price_after"],
            "label": p.get("label", "high"),
            "label_short": p.get("label_short", "?"),
            "magnitude": p["magnitude"],
            "interpreted": p.get("interpreted", True),
        } for p in pivots])

        color_scale = alt.Scale(
            domain=["higher_high", "lower_high", "higher_low", "lower_low",
                    "high", "low"],
            range=[tt.GREEN, tt.AMBER, tt.CYAN, tt.RED, tt.GREEN, tt.RED],
        )
        interpreted_df = adf[adf["interpreted"]]
        uninterpreted_df = adf[~adf["interpreted"]]

        if not uninterpreted_df.empty:
            small_dots = alt.Chart(uninterpreted_df).mark_circle(
                size=60, opacity=0.5, stroke=tt.DIM, strokeWidth=1
            ).encode(
                x="date:T", y="price:Q",
                color=alt.Color("label:N", scale=color_scale, legend=None),
                tooltip=[alt.Tooltip("date:T"),
                         alt.Tooltip("price:Q", format=".4f"),
                         alt.Tooltip("label_short:N", title="pivot"),
                         alt.Tooltip("magnitude:Q", format="+.4f")],
            )
            layers.append(small_dots)
        if not interpreted_df.empty:
            big_dots = alt.Chart(interpreted_df).mark_circle(
                size=160, stroke=tt.AMBER, strokeWidth=2
            ).encode(
                x="date:T", y="price:Q",
                color=alt.Color("label:N", scale=color_scale, legend=None),
                tooltip=[alt.Tooltip("date:T"),
                         alt.Tooltip("price:Q", format=".4f"),
                         alt.Tooltip("label_short:N", title="pivot"),
                         alt.Tooltip("magnitude:Q", format="+.4f")],
            )
            layers.append(big_dots)

    chart = alt.layer(*layers).resolve_scale(y="shared").properties(
        height=300, background=tt.BG, padding={"top": 20, "right": 12,
                                               "bottom": 8, "left": 8},
    ).configure_axis(
        domainColor=tt.BORDER, tickColor=tt.BORDER,
    ).configure_view(strokeWidth=0)

    st.altair_chart(chart, use_container_width=True)

    if pivots:
        n_interp = sum(1 for p in pivots if p.get("interpreted"))
        # Legend caption
        st.caption(
            f"📍 {len(pivots)} pivot points detected · "
            f"{n_interp} interpreted (largest dots) · "
            "🟢 higher high · 🟠 lower high · 🔵 higher low · 🔴 lower low"
        )


def render_inspector_panel(token_id: str, history: list, question: str,
                           slug: str = ""):
    st.markdown("### 🔬 Inspector — pivot catalysts")
    st.caption("Top pivot points on the lifetime curve (HH/LH/HL/LL), each "
               "interpreted by Nemotron against news headlines from a ±7-day "
               "window. News pulled from Google News RSS (primary, mainstream "
               "sources) with GDELT as fallback (long-tail / regional).")
    # Threshold matches find_pivots' minimum (2*PIVOT_LOOKBACK+1 = 11),
    # plus a small buffer so the chart panel above didn't already detect
    # pivots that this panel claims can't exist.
    if len(history) < 12:
        st.info("Not enough lifetime price data to detect pivots yet "
                "(need ~12 daily points).")
        return None

    analysis = get_inspector_analysis(token_id, len(history), question)
    anomalies = analysis.get("anomalies", [])

    # Record health in session_state so the sidebar can mark this market
    health = analysis.get("health") or inspector.summarize_health(anomalies)
    if slug:
        st.session_state.setdefault("market_health", {})[slug] = health

    if not anomalies:
        st.info("No structural pivots found on this curve.")
        return analysis

    # Always hide pivots without a real catalyst — no toggle, no exceptions.
    useful = [a for a in anomalies
              if inspector.is_useful_explanation(a.get("explanation"))]

    if not useful:
        st.warning("All pivots came back without catalysts (no relevant news "
                   "found in any ±7-day window). This market is not demo-ready "
                   f"as-is. Health: {health['good']}/{health['total']}.")
        return analysis

    if len(useful) < len(anomalies):
        st.caption(f"Showing {len(useful)} of {len(anomalies)} pivots with real "
                   f"catalysts · {len(anomalies)-len(useful)} hidden as empty.")
    show = useful

    for a in show:
        st.markdown(tt.anomaly_card(a), unsafe_allow_html=True)
        exp = a.get("explanation", {}) or {}
        articles = exp.get("articles") or []
        if articles:
            with st.expander(f"  → headlines for {a['date'].date()}",
                             expanded=False):
                for art in articles:
                    title = art.get("title", "")[:200]
                    url = art.get("url", "")
                    domain = art.get("domain", "?")
                    if url:
                        st.markdown(f"- [{title}]({url})  _({domain})_")
                    else:
                        st.markdown(f"- {title} _({domain})_")
    return analysis


def render_price_journey(token_id: str, history: list, question: str):
    """Replaces the band-by-band table with an AI-narrated paragraph about
    how the market traveled across conviction regimes over its lifetime."""
    st.markdown("### 📖 price journey")
    if not history or len(history) < 10:
        st.caption("Not enough price history to narrate a journey.")
        return
    prices = [p for _, p in history]
    st.caption(f"Lifetime range {min(prices):.3f}–{max(prices):.3f} · "
               f"{len(history)} daily points · narrated by Nemotron")
    journey = get_price_journey(token_id, len(history), question)
    st.markdown(
        f"<div class='tt-panel' style='border-left: 3px solid {tt.AMBER};"
        f"padding: 0.7rem 0.9rem; line-height: 1.5'>"
        f"{journey}"
        f"</div>",
        unsafe_allow_html=True,
    )


def render_agent_context(slug, predictions):
    matches = [p for p in predictions
               if (p.get("our_prediction") or {}).get("market") == slug]
    if not matches:
        return
    st.markdown("### agent context — prior flags on this market")
    for p in matches[-5:]:
        pred = p.get("our_prediction") or {}
        verdict = p.get("verdict", "(open)")
        score = p.get("contradiction_score", "?")
        ts = p.get("logged_at", "?")
        with st.expander(f"{ts} — score {score} — {verdict}", expanded=False):
            st.markdown(f"**Direction:** {pred.get('direction', '?')}")
            st.markdown(f"**Confidence:** {pred.get('confidence', '?')}")
            st.markdown(f"**Reasoning:** {pred.get('reasoning', '?')}")
            if "analista_reasoning" in p and p["analista_reasoning"]:
                st.markdown(f"**Analista:** _{p['analista_reasoning'].get('reasoning', '')}_")
            if p.get("case_file"):
                st.markdown("**Case file (excerpt):**")
                st.markdown("> " + p["case_file"][:500].replace("\n", "\n> ") + "…")


def render_loop_activity(ticks):
    st.markdown("### 🛰️ loop activity — what the agents are seeing")
    if not ticks:
        st.info("No ticks logged yet. Start the loop with `python run.py` to "
                "see live activity here.")
        return

    # AI briefing of the recent loop state
    last_ts = ticks[-1].get("logged_at", "")
    try:
        briefing = get_loop_briefing(len(ticks), last_ts)
        st.markdown(
            f"<div class='tt-panel' style='border-left: 3px solid {tt.CYAN};"
            f"padding: 0.7rem 0.9rem; margin-bottom: 0.5rem; line-height: 1.5'>"
            f"<div class='tt-label' style='margin-bottom:0.3rem'>"
            f"Narrator · current state</div>"
            f"{briefing}"
            f"</div>",
            unsafe_allow_html=True,
        )
    except Exception as e:
        st.caption(f"(briefing failed: {type(e).__name__})")

    # Raw tick data below the briefing (collapsed by default)
    recent = ticks[-12:]
    df = pd.DataFrame([{
        "logged_at": t.get("logged_at", "")[:19],
        "tick": t.get("tick"),
        "contradiction": t.get("contradiction_score"),
        "fossil_%": t.get("fossil_pct"),
        "mean_tone": t.get("mean_tone"),
        "grid_src": t.get("cenace_source", "—"),
    } for t in reversed(recent)])
    with st.expander(f"raw tick log (last {len(recent)} ticks)", expanded=False):
        st.dataframe(df, use_container_width=True, height=280, hide_index=True)


def render_market_view(slug, market, predictions):
    question = market.get("question", slug)
    token_id = market.get("yes_token_id", "")

    st.markdown(f"## {question}")
    st.caption(f"slug: `{slug}` · token_id: `{token_id[:20]}…`")

    # ----- live ticker (ws-driven, auto-refresh every 3s) -----
    if token_id:
        render_live_ticker(token_id, fallback_odds=market.get("yes_odds", 0))
    else:
        st.warning("No yes_token_id on this market — can't subscribe to ws feed.")

    # ----- lifetime chart + inspector -----
    history = get_lifetime(token_id) if token_id else []
    if not history:
        st.warning("No lifetime price history via CLOB.")
        return

    # AI market briefing — what is this market, what does the price mean
    try:
        briefing = get_market_briefing(token_id, len(history), question,
                                       market.get("yes_odds", 0))
        st.markdown(
            f"<div class='tt-panel' style='border-left: 3px solid {tt.AMBER};"
            f"padding: 0.8rem 1rem; margin-bottom: 0.6rem; line-height: 1.5;"
            f"font-size: 0.92rem'>"
            f"<div class='tt-label' style='margin-bottom:0.4rem'>"
            f"Narrator · market briefing</div>"
            f"{briefing}"
            f"</div>",
            unsafe_allow_html=True,
        )
    except Exception as e:
        st.caption(f"(briefing failed: {type(e).__name__})")

    cols = st.columns([3, 2])
    with cols[0]:
        st.markdown("### lifetime price + rolling band")
        # Compute bands first so chart can show them; pre-fetch the analysis
        # (inspector caches it, so render_inspector_panel won't pay again)
        analysis = get_inspector_analysis(token_id, len(history), question)
        render_lifetime_with_anomalies(history, analysis)
    with cols[1]:
        c1, c2 = st.columns(2)
        c1.markdown(tt.metric_block("LIFETIME PTS", str(len(history))),
                    unsafe_allow_html=True)
        prices = [p for _, p in history]
        c2.markdown(tt.metric_block(
            "RANGE", f"{min(prices):.2f}–{max(prices):.2f}"),
            unsafe_allow_html=True)
        c3, c4 = st.columns(2)
        c3.markdown(tt.metric_block("REST 24h VOL",
                                    tt.fmt_volume(market.get("volume", 0))),
                    unsafe_allow_html=True)
        c4.markdown(tt.metric_block("REST YES",
                                    f"{market.get('yes_odds', 0):.3f}"),
                    unsafe_allow_html=True)
        h7 = market.get("history_7d") or {}
        if h7.get("points"):
            df7 = pd.DataFrame(h7["points"], columns=["ts", "price"])
            df7["t"] = pd.to_datetime(df7["ts"], unit="s")
            df7 = df7.set_index("t")[["price"]]
            st.markdown("<div class='tt-label'>7d trajectory</div>",
                        unsafe_allow_html=True)
            st.line_chart(df7, height=140)

    render_inspector_panel(token_id, history, question, slug=slug)
    render_price_journey(token_id, history, question)
    render_agent_context(slug, predictions)


# ----------------------- Main -----------------------

def main():
    st.set_page_config(page_title="Caer y Reconstruir — Market Investigator",
                       layout="wide",
                       initial_sidebar_state="expanded")
    tt.inject_theme()

    data = get_markets()
    markets = data.get("markets", {}) or {}
    predictions = load_jsonl(str(PRED_LOG))
    ticks = load_jsonl(str(TICK_LOG))

    render_hero_strip(predictions, ticks)
    selected = render_sidebar(markets)

    if selected and selected in markets:
        render_market_view(selected, markets[selected], predictions)
    elif not selected:
        st.info("Pick a market from the sidebar to investigate.")

    st.markdown("---")
    render_loop_activity(ticks)


if __name__ == "__main__":
    main()
