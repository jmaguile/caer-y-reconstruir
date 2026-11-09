"""Bloomberg-terminal styling for the Streamlit dashboard.

Two layers:
  1. The .streamlit/config.toml sets base palette + monospace font.
  2. inject_theme() pushes a CSS block tightening Streamlit's default
     padding, restyling headers / metrics / expanders, and adding
     the amber-on-black trading-terminal feel.
The render helpers (pill, metric_block, tick_row) emit small HTML
fragments via st.markdown(unsafe_allow_html=True) so we can sidestep
st.metric's truncation and the bulky default expander chrome.
"""
from __future__ import annotations

import streamlit as st

# Bloomberg-ish palette
AMBER = "#FFA500"
AMBER_DIM = "#B97400"
GREEN = "#33D17A"
RED = "#FF3B30"
CYAN = "#5BC0DE"
DIM = "#7A7A7A"
BG = "#0A0A0A"
PANEL = "#141414"
BORDER = "#222222"
TEXT = "#E6E6E6"

_CSS = f"""
<style>
/* Push content down past Streamlit's sticky header (Stop/Deploy buttons live
   there and would otherwise overlap the hero strip's first tile). */
.block-container {{
    padding-top: 3.5rem !important;
    padding-bottom: 1rem !important;
    max-width: 100% !important;
}}
/* Soften Streamlit's header bar so the Stop button still works but doesn't
   draw attention away from our hero strip below it. */
header[data-testid="stHeader"] {{
    background-color: rgba(10, 10, 10, 0.85) !important;
    backdrop-filter: blur(4px);
}}

/* Header: amber accent, no underline */
h1, h2, h3 {{
    color: {AMBER} !important;
    font-family: monospace !important;
    letter-spacing: 0.02em;
    margin-bottom: 0.3rem !important;
}}
h1 {{ font-size: 1.35rem !important; }}
h2 {{ font-size: 1.10rem !important; }}
h3 {{ font-size: 0.95rem !important; border-top: 1px solid {BORDER}; padding-top: 0.4rem; }}

/* All caption text in dim */
.stCaption, [data-testid="stCaptionContainer"] {{
    color: {DIM} !important;
    font-size: 0.78rem !important;
}}

/* Sidebar: tighter, panel bg */
section[data-testid="stSidebar"] > div {{
    background-color: {PANEL} !important;
    padding-top: 0.5rem !important;
}}
section[data-testid="stSidebar"] .stRadio > label {{
    padding: 0.15rem 0.4rem !important;
    border-bottom: 1px solid {BORDER};
    cursor: pointer;
    font-size: 0.82rem;
}}

/* Native st.metric: shrink + dim label, amber value */
[data-testid="stMetric"] {{
    background-color: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 3px;
    padding: 0.4rem 0.6rem;
}}
[data-testid="stMetricLabel"] {{
    color: {DIM} !important;
    font-size: 0.72rem !important;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}}
[data-testid="stMetricValue"] {{
    color: {AMBER} !important;
    font-size: 1.20rem !important;
    font-weight: 600;
}}

/* Expanders */
[data-testid="stExpander"] {{
    background-color: {PANEL} !important;
    border: 1px solid {BORDER} !important;
    border-radius: 3px !important;
}}
[data-testid="stExpander"] summary {{
    font-size: 0.85rem !important;
    color: {AMBER_DIM} !important;
}}

/* Buttons: amber outlined */
.stButton button {{
    background-color: transparent !important;
    color: {AMBER} !important;
    border: 1px solid {AMBER_DIM} !important;
    border-radius: 2px !important;
    font-family: monospace !important;
    font-size: 0.8rem !important;
    padding: 0.2rem 0.7rem !important;
}}
.stButton button:hover {{
    background-color: {AMBER_DIM} !important;
    color: {BG} !important;
    border-color: {AMBER} !important;
}}

/* Custom panel class — used by metric_block, hero_strip, tick_row */
.tt-panel {{
    background-color: {PANEL};
    border: 1px solid {BORDER};
    border-radius: 3px;
    padding: 0.4rem 0.6rem;
    margin-bottom: 0.3rem;
    font-family: monospace;
    font-size: 0.85rem;
    color: {TEXT};
}}
.tt-label {{
    color: {DIM};
    font-size: 0.68rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
}}
.tt-value {{
    color: {AMBER};
    font-size: 1.15rem;
    font-weight: 600;
    margin-top: 0.05rem;
}}
.tt-sub {{
    color: {DIM};
    font-size: 0.72rem;
    margin-top: 0.1rem;
}}
.tt-pill {{
    display: inline-block;
    padding: 0.05rem 0.4rem;
    border-radius: 2px;
    font-size: 0.72rem;
    font-weight: 600;
    margin-right: 0.3rem;
}}
.tt-tick-row {{
    display: grid;
    grid-template-columns: 100px 60px 60px 60px 1fr;
    gap: 0.5rem;
    padding: 0.15rem 0.4rem;
    border-bottom: 1px dotted {BORDER};
    font-size: 0.78rem;
}}
.tt-anomaly {{
    border-left: 3px solid {AMBER};
    background-color: {PANEL};
    padding: 0.5rem 0.7rem;
    margin-bottom: 0.4rem;
    border-radius: 0 3px 3px 0;
}}
.tt-anomaly .date {{ color: {AMBER}; font-weight: 600; font-size: 0.88rem; }}
.tt-anomaly .meta {{ color: {DIM}; font-size: 0.72rem; margin-bottom: 0.25rem; }}
.tt-anomaly .interp {{ color: {TEXT}; font-size: 0.85rem; }}
.tt-anomaly .source {{ color: {CYAN}; font-size: 0.72rem; margin-top: 0.2rem; }}

/* Live indicator dot */
.tt-live-dot {{
    display: inline-block;
    width: 8px; height: 8px;
    border-radius: 50%;
    background-color: {GREEN};
    box-shadow: 0 0 6px {GREEN};
    margin-right: 0.4rem;
    animation: tt-pulse 1.4s ease-in-out infinite;
}}
.tt-live-dot.dead {{ background-color: {RED}; box-shadow: 0 0 6px {RED}; animation: none; }}
@keyframes tt-pulse {{
    0%, 100% {{ opacity: 1; }}
    50% {{ opacity: 0.35; }}
}}
</style>
"""


def inject_theme() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)


def _odds_color(odds: float) -> str:
    if odds >= 0.5:
        return GREEN
    if odds >= 0.2:
        return AMBER
    return RED


def odds_pill(odds: float) -> str:
    color = _odds_color(odds)
    return (f"<span class='tt-pill' style='background:{color}1A;color:{color};"
            f"border:1px solid {color}'>{odds:.2f}</span>")


def fmt_volume(v: float) -> str:
    if v >= 1_000_000:
        return f"${v/1_000_000:.1f}M"
    if v >= 1_000:
        return f"${v/1_000:.1f}K"
    return f"${v:.0f}"


def metric_block(label: str, value: str, sub: str = "",
                 value_color: str = AMBER) -> str:
    return (f"<div class='tt-panel'>"
            f"<div class='tt-label'>{label}</div>"
            f"<div class='tt-value' style='color:{value_color}'>{value}</div>"
            + (f"<div class='tt-sub'>{sub}</div>" if sub else "")
            + "</div>")


def hero_tile(label: str, value: str, sub: str = "", color: str = AMBER) -> str:
    return metric_block(label, value, sub, value_color=color)


def live_dot(connected: bool, label: str = "") -> str:
    cls = "tt-live-dot" if connected else "tt-live-dot dead"
    return f"<span class='{cls}'></span><span style='color:{DIM};font-size:0.78rem'>{label}</span>"


def sparkline_unicode(prices: list[float], width: int = 12) -> str:
    """Tiny inline unicode sparkline — fits next to a market label in the sidebar."""
    if not prices:
        return ""
    bars = "▁▂▃▄▅▆▇█"
    lo, hi = min(prices), max(prices)
    rng = (hi - lo) or 1.0
    # Resample to `width` points
    if len(prices) <= width:
        sample = prices
    else:
        step = len(prices) / width
        sample = [prices[int(i * step)] for i in range(width)]
    return "".join(bars[min(7, int((p - lo) / rng * 7))] for p in sample)


_LABEL_COLOR = {
    "higher_high": GREEN, "lower_high": AMBER,
    "higher_low": CYAN,   "lower_low": RED,
    "high": GREEN, "low": RED,
}

_LABEL_FULL = {
    "higher_high": "higher high · uptrend continuation",
    "lower_high":  "lower high · downtrend signal",
    "higher_low":  "higher low · uptrend signal",
    "lower_low":   "lower low · downtrend continuation",
    "high": "first pivot high", "low": "first pivot low",
}


def anomaly_card(a: dict) -> str:
    """Renders a pivot card. Uses pivot label (HH/LH/HL/LL) if present, falls
    back to z-score direction for older analyses."""
    exp = a.get("explanation", {}) or {}
    interp = exp.get("interpretation", "(no interpretation)")
    confidence = exp.get("confidence", 0)
    source = exp.get("catalyst_source", "none")
    via_raw = exp.get("source", "")
    via = ""
    if via_raw == "google_news":
        via = "<span style='color:" + DIM + "'> via google news</span>"
    elif via_raw == "gdelt":
        via = "<span style='color:" + DIM + "'> via gdelt</span>"
    direction = "▲" if a["magnitude"] > 0 else "▼"
    color = GREEN if a["magnitude"] > 0 else RED

    label = a.get("label")
    label_short = a.get("label_short", "")
    label_color = _LABEL_COLOR.get(label, color)
    label_full = _LABEL_FULL.get(label, "")
    z = a.get("z_score", 0)

    pill = ""
    if label_short:
        pill = (f"<span class='tt-pill' style='background:{label_color}1A;"
                f"color:{label_color};border:1px solid {label_color}'>"
                f"{label_short}</span>")

    return (
        f"<div class='tt-anomaly' style='border-left-color:{label_color}'>"
        f"<div class='date'>{pill}{a['date'].date()} "
        f"<span style='color:{color}'>{direction}{abs(a['magnitude']):.3f}</span> "
        f"<span style='color:{DIM};font-weight:400;font-size:0.78rem'>"
        f"(z={z:+.1f})</span></div>"
        f"<div class='meta'>{a['price_before']:.3f} → {a['price_after']:.3f}, "
        f"vs rolling mean {a['rolling_mean']:.3f}"
        + (f" · <span style='color:{label_color}'>{label_full}</span>"
           if label_full else "")
        + "</div>"
        f"<div class='interp'>{interp}</div>"
        f"<div class='source'>catalyst: {source}{via} · model conf {confidence}</div>"
        f"</div>"
    )
