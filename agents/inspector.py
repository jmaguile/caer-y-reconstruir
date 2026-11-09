"""El Inspector — chart-anomaly hunter.

For a given market's lifetime price series, computes a rolling mean & std,
identifies the top-K outlier days (|z-score| above threshold), and for each
anomaly asks Nemotron — grounded in GDELT headlines for that ±3-day window —
to explain what likely happened.

This is the agent that gives the Bloomberg-terminal feel: every chart has
annotations and a one-line story for each unusual move. Cached aggressively
per (token_id, history_length) so it doesn't re-run on every Streamlit rerun.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from fetchers import gdelt, google_news
from nemotron_client import reason_nemotron, parse_json_blob

ROLLING_WINDOW_DAYS = 14
MIN_PERIODS = 7
Z_THRESHOLD = 1.8
EDGE_TRIM_DAYS = 14   # ignore anomalies in first/last N days (noisy edges)

# Pivot detection: a point is a pivot high if it's the max of the swing
# window [-PIVOT_LOOKBACK, +PIVOT_LOOKBACK], pivot low if it's the min.
# 5 → 11-day swing window → roughly weekly pivots.
PIVOT_LOOKBACK = 5
# Cap GDELT-interpreted pivots to keep first-load reasonable. Each
# interpretation pays 31s GDELT cooldown + 1 Nemotron call, so K=4 → ~2min
# cold load per market. Cached 15min after — warm up demo markets first.
MAX_INTERPRETED_PIVOTS = 4

# Filter out tiny moves that hit Z_THRESHOLD only because rolling std is
# microscopic. For a market trading 0.007 → 0.009, |z| can hit +3.5 but the
# absolute move is rounding noise. Require the move to be at least 10% of
# the lifetime range, with a hard floor of 0.01 (one cent of YES odds).
MIN_MAGNITUDE_FLOOR = 0.01
MIN_MAGNITUDE_FRAC_OF_RANGE = 0.10

# Stop-word list for smart GDELT query construction. Keeps proper nouns and
# distinctive terms, drops connectives.
_STOP_WORDS = {
    "will", "the", "be", "by", "in", "of", "to", "and", "or", "a", "an", "at",
    "after", "before", "is", "are", "was", "were", "on", "for", "with", "as",
    "if", "then", "than", "this", "that", "these", "those", "have", "has",
    "had", "do", "does", "did", "would", "could", "should", "may", "might",
    "must", "can", "shall", "us", "any", "all", "no", "not", "yes", "from",
    "into", "out", "over", "under",
}


def _smart_gdelt_query(question: str) -> str:
    """Extract distinctive search terms from a market question. Drops stop
    words and orders capitalized terms (proper nouns) first, capping at 4
    terms so GDELT's query parser stays happy. Falls back to 'economy' if
    nothing usable remains."""
    cleaned = (question.replace("?", "").replace(",", "").replace("'s", "")
                       .replace(".", "").replace("(", "").replace(")", ""))
    words = [w for w in cleaned.split() if w and w.lower() not in _STOP_WORDS]
    capitalized = [w for w in words if w[:1].isupper() and not w.isupper()]
    other = [w for w in words if w not in capitalized]
    ordered, seen = [], set()
    for w in capitalized + other:
        if w.lower() not in seen:
            ordered.append(w)
            seen.add(w.lower())
    return " ".join(ordered[:4]) or "economy"

SYSTEM = (
    "You are El Inspector, a market analyst. Given a single unusual price "
    "move and a few news headlines from that date range, you produce a "
    "one-sentence plain-English explanation of what likely caused the move. "
    "If the headlines don't explain it, say 'no clear catalyst' rather than "
    "speculating. Reply with valid JSON only, no prose, no markdown fences."
)

PROMPT = """Market question: {question}

On {date}, the YES odds moved from {price_before:.3f} to {price_after:.3f}
(a {direction} of {magnitude:.3f}, which is {z_score:.1f} standard deviations
from the {window}-day rolling mean).

Headlines from {window_start} to {window_end} ({n_articles} articles, ±7 days
around the move, pulled from Google News RSS with GDELT fallback so coverage
spans mainstream and long-tail sources):
{headlines}

Reply with this JSON shape only:
{{
  "interpretation": "<one-sentence plain-English explanation, or 'no clear catalyst'>",
  "confidence": <0.0 to 1.0>,
  "catalyst_source": "<best matching headline source domain, or 'none'>"
}}"""


def _series_to_df(history: list[tuple[int, float]]) -> pd.DataFrame:
    df = pd.DataFrame(history, columns=["ts", "price"])
    df["date"] = pd.to_datetime(df["ts"], unit="s", utc=True)
    df = df.set_index("date").sort_index()
    return df[["price"]]


def compute_bands(history: list[tuple[int, float]]) -> pd.DataFrame:
    """Returns df with columns: price, rolling_mean, upper, lower, z_score."""
    if len(history) < MIN_PERIODS + 2:
        return pd.DataFrame()
    df = _series_to_df(history)
    df["rolling_mean"] = df["price"].rolling(ROLLING_WINDOW_DAYS,
                                             min_periods=MIN_PERIODS).mean()
    df["rolling_std"] = df["price"].rolling(ROLLING_WINDOW_DAYS,
                                            min_periods=MIN_PERIODS).std()
    df["upper"] = df["rolling_mean"] + 2 * df["rolling_std"]
    df["lower"] = df["rolling_mean"] - 2 * df["rolling_std"]
    df["z_score"] = (df["price"] - df["rolling_mean"]) / df["rolling_std"].replace(0, np.nan)
    return df


def find_anomalies(history: list[tuple[int, float]], k: int = 3) -> list[dict]:
    """Top-K anomaly dates by |z-score|, trimming the noisy edges. Requires
    the absolute price move to clear MIN_MAGNITUDE so we don't fire on
    rounding noise on low-vol markets."""
    df = compute_bands(history)
    if df.empty or df["z_score"].isna().all():
        return []
    # Trim edges
    if len(df) > 2 * EDGE_TRIM_DAYS:
        core = df.iloc[EDGE_TRIM_DAYS:-EDGE_TRIM_DAYS].copy()
    else:
        core = df.copy()
    core = core.dropna(subset=["z_score"])
    core["abs_z"] = core["z_score"].abs()
    core = core[core["abs_z"] >= Z_THRESHOLD]

    # Magnitude floor: move must be material relative to lifetime range
    lifetime_range = float(df["price"].max() - df["price"].min())
    min_magnitude = max(MIN_MAGNITUDE_FLOOR,
                        lifetime_range * MIN_MAGNITUDE_FRAC_OF_RANGE)
    prior = df["price"].shift(1)
    core["mag_abs"] = (df.loc[core.index, "price"] - prior.loc[core.index]).abs()
    core = core[core["mag_abs"] >= min_magnitude]
    if core.empty:
        return []
    # Suppress neighbors: keep only the local max in each 7-day window
    core = core.sort_index()
    keep = []
    last_kept_idx = None
    for idx, row in core.sort_values("abs_z", ascending=False).iterrows():
        if any(abs((idx - k).total_seconds()) < 7 * 86400 for k in keep):
            continue
        keep.append(idx)
        if len(keep) >= k:
            break

    anomalies = []
    for d in keep:
        row = df.loc[d]
        # find price 1-day prior for the magnitude readout
        prior = df.loc[df.index < d]
        price_before = float(prior.iloc[-1]["price"]) if not prior.empty else float(row["price"])
        anomalies.append({
            "date": d.to_pydatetime(),
            "price_before": round(price_before, 4),
            "price_after": round(float(row["price"]), 4),
            "magnitude": round(float(row["price"]) - price_before, 4),
            "z_score": round(float(row["z_score"]), 2),
            "rolling_mean": round(float(row["rolling_mean"]), 4),
        })
    # Sort chronologically for display
    anomalies.sort(key=lambda a: a["date"])
    return anomalies


def interpret_anomaly(anomaly: dict, question: str) -> dict:
    """Find news around the anomaly date and ask Nemotron for an explanation.

    Dual-source: Google News RSS (primary, no auth, no rate limit) is tried
    first because it's reliable and fast for date-anchored queries. GDELT is
    the fallback because its 30s throttle and patchy coverage make it the
    weaker partner for per-pivot lookups. If both come back empty, we honestly
    report 'no news found in either source' rather than fabricate.
    """
    target: datetime = anomaly["date"]
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    keywords = _smart_gdelt_query(question)

    # Primary: Google News RSS — no rate limit, mainstream-source bias
    res = google_news.fetch_around_date(target, keywords,
                                        window_days=7, limit=10) or {}
    articles = res.get("articles", [])
    src_label = "google_news"

    # Fallback: GDELT, if Google News returned nothing
    if not articles:
        gd = gdelt.fetch_around_date(target, keywords,
                                     window_days=7, limit=10) or {}
        if not gd.get("throttled") and gd.get("articles"):
            articles = gd["articles"]
            src_label = "gdelt"

    if not articles:
        return {
            "interpretation": ("no news found in Google News or GDELT for "
                               "any day within ±7 days of this move"),
            "confidence": 0.0,
            "catalyst_source": "none",
            "articles": [],
        }
    headlines = "\n".join(f"  - {a.get('title', '')[:140]}  ({a.get('domain', '?')})"
                          for a in articles)
    from datetime import timedelta
    window_start = (target - timedelta(days=7)).date().isoformat()
    window_end = (target + timedelta(days=7)).date().isoformat()
    direction = "increase" if anomaly["magnitude"] > 0 else "decrease"
    raw = reason_nemotron(
        PROMPT.format(
            question=question[:160],
            date=target.date().isoformat(),
            price_before=anomaly["price_before"],
            price_after=anomaly["price_after"],
            direction=direction,
            magnitude=abs(anomaly["magnitude"]),
            z_score=anomaly["z_score"],
            window=ROLLING_WINDOW_DAYS,
            window_start=window_start,
            window_end=window_end,
            n_articles=len(articles),
            headlines=headlines,
        ),
        system=SYSTEM,
    )
    parsed = parse_json_blob(raw) or {}
    return {
        "interpretation": parsed.get("interpretation",
                                     "(model returned no interpretation)"),
        "confidence": parsed.get("confidence", 0.0),
        "catalyst_source": parsed.get("catalyst_source", "none"),
        "source": src_label,
        "articles": articles[:5],
    }


def find_pivots(history: list[tuple[int, float]],
                lookback: int = PIVOT_LOOKBACK) -> list[dict]:
    """Detect pivot highs and lows using a ±lookback swing window. Labels each
    pivot as higher_high / lower_high / higher_low / lower_low based on
    comparison to the prior same-kind pivot. This is the price-action way to
    surface structural turning points — what traders read off a chart.

    Returns chronologically sorted pivots with: date, price, kind, label,
    label_short, magnitude (vs previous price), z_score (vs rolling stats),
    rolling_mean, prominence (signed distance vs neighbor pivots).
    """
    if len(history) < 2 * lookback + 1:
        return []
    bands = compute_bands(history)
    if bands.empty:
        return []
    prices = bands["price"].values
    dates = bands.index.to_list()
    n = len(prices)

    raw = []
    for i in range(lookback, n - lookback):
        window = prices[i - lookback:i + lookback + 1]
        center = prices[i]
        if center == max(window) and center >= prices[i - 1]:
            raw.append({"i": i, "kind": "high", "price": float(center),
                        "date": dates[i]})
        elif center == min(window) and center <= prices[i - 1]:
            raw.append({"i": i, "kind": "low", "price": float(center),
                        "date": dates[i]})

    # Dedupe: avoid back-to-back highs or lows within the lookback window
    deduped = []
    for p in raw:
        if deduped and deduped[-1]["kind"] == p["kind"]:
            if p["kind"] == "high" and p["price"] > deduped[-1]["price"]:
                deduped[-1] = p
            elif p["kind"] == "low" and p["price"] < deduped[-1]["price"]:
                deduped[-1] = p
            continue
        deduped.append(p)

    # Label each pivot vs prior same-kind pivot
    last_high = last_low = None
    for p in deduped:
        if p["kind"] == "high":
            if last_high is None:
                p["label"], p["label_short"] = "high", "H"
            elif p["price"] > last_high:
                p["label"], p["label_short"] = "higher_high", "HH"
            else:
                p["label"], p["label_short"] = "lower_high", "LH"
            last_high = p["price"]
        else:
            if last_low is None:
                p["label"], p["label_short"] = "low", "L"
            elif p["price"] > last_low:
                p["label"], p["label_short"] = "higher_low", "HL"
            else:
                p["label"], p["label_short"] = "lower_low", "LL"
            last_low = p["price"]

    # Enrich with rolling-stats context + prominence (signed distance vs
    # the mean of neighbor pivots; bigger = more visually significant)
    out = []
    for j, p in enumerate(deduped):
        idx = p["i"]
        row = bands.iloc[idx]
        prior_price = float(prices[idx - 1]) if idx > 0 else float(p["price"])
        magnitude = p["price"] - prior_price
        z = row.get("z_score")
        if pd.notna(z):
            z = round(float(z), 2)
        else:
            z = 0.0
        prev_neighbor = deduped[j - 1]["price"] if j > 0 else p["price"]
        next_neighbor = deduped[j + 1]["price"] if j < len(deduped) - 1 else p["price"]
        prominence = abs(p["price"] - (prev_neighbor + next_neighbor) / 2)
        out.append({
            "date": p["date"].to_pydatetime(),
            "kind": p["kind"],
            "label": p["label"],
            "label_short": p["label_short"],
            "price_after": round(p["price"], 4),
            "price_before": round(prior_price, 4),
            "magnitude": round(magnitude, 4),
            "z_score": z,
            "rolling_mean": round(float(row["rolling_mean"]), 4)
                            if pd.notna(row.get("rolling_mean")) else p["price"],
            "prominence": round(prominence, 4),
        })
    return out


def analyze(history: list[tuple[int, float]], question: str,
            k: int = MAX_INTERPRETED_PIVOTS, interpret: bool = True) -> dict:
    """Full pipeline: bands, pivot points (HH/LH/HL/LL), and Nemotron
    interpretations for the top-K most prominent pivots. Returns
    {"bands": DataFrame, "pivots": [all_pivots], "anomalies": [top_k_interpreted]}.

    'anomalies' key is preserved for backwards compat with the dashboard's
    existing card renderer — it now holds the interpreted subset of pivots,
    sorted chronologically. The full pivot list (uninterpreted) lives in
    'pivots' for chart annotation."""
    bands = compute_bands(history)
    pivots = find_pivots(history)

    # Pick top-K by prominence for GDELT interpretation
    top = sorted(pivots, key=lambda p: p["prominence"], reverse=True)[:k]
    top_dates = {p["date"] for p in top}
    top.sort(key=lambda p: p["date"])

    if interpret:
        for i, a in enumerate(top):
            try:
                a["explanation"] = interpret_anomaly(a, question)
            except Exception as e:
                a["explanation"] = {
                    "interpretation": f"(interpretation failed: {type(e).__name__})",
                    "confidence": 0.0,
                    "catalyst_source": "none",
                    "articles": [],
                }
            # Google News has no enforced rate limit; small politeness sleep
            # so we don't burst-hit it on first-load (4 calls = ~2s total).
            if i < len(top) - 1:
                time.sleep(0.5)

    # Mark which pivots got interpretations (for chart rendering)
    for p in pivots:
        p["interpreted"] = p["date"] in top_dates

    return {"bands": bands, "pivots": pivots, "anomalies": top,
            "health": summarize_health(top)}


def is_useful_explanation(exp: dict | None) -> bool:
    """True iff this explanation has a real, demoable catalyst — not throttled,
    not empty articles, not a 'no clear catalyst' / 'no GDELT' fallback."""
    if not exp:
        return False
    if exp.get("throttled"):
        return False
    if not exp.get("articles"):
        return False
    interp = (exp.get("interpretation") or "").lower()
    if not interp:
        return False
    for skip in ("no clear catalyst", "no gdelt", "interpretation failed",
                 "no articles", "rate-limit", "no news found"):
        if skip in interp:
            return False
    return True


def summarize_health(anomalies: list[dict]) -> dict:
    """Aggregate explanation quality across a market's interpreted pivots.
    Returns {label: 'good' | 'partial' | 'empty' | 'unknown', good, total}.
    Used by the dashboard sidebar to mark demo-ready markets."""
    if not anomalies:
        return {"label": "unknown", "good": 0, "total": 0}
    total = len(anomalies)
    good = sum(1 for a in anomalies if is_useful_explanation(a.get("explanation")))
    if good == 0:
        label = "empty"
    elif good >= max(1, (total + 1) // 2):
        label = "good"
    else:
        label = "partial"
    return {"label": label, "good": good, "total": total}
