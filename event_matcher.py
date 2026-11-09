"""Historical event matcher.

When testigo picks a Polymarket prediction, look back over that market's full
lifetime price curve for the last time YES odds were within ±0.02 of the
current value (excluding the past 7 days). Map that date against a curated
energy/climate event timeline. Fall back to a date-anchored GDELT query if
no timeline event lands within ±3 days, gated by ENABLE_GDELT_ECHO_FALLBACK.

Writes state['historical_echo'] = {
    market, current_odds, matched_date, event: {date, headline, context},
    source: 'timeline' | 'gdelt-fallback'
}
or leaves it unset when no match is found.
"""
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fetchers import gdelt, polymarket

TIMELINE_PATH = Path(__file__).resolve().parent / "events_timeline.json"
ODDS_TOLERANCE = 0.02
RECENT_EXCLUSION_DAYS = 7
EVENT_WINDOW_DAYS = 3

_timeline_cache: list[dict] | None = None


def _load_timeline() -> list[dict]:
    global _timeline_cache
    if _timeline_cache is not None:
        return _timeline_cache
    if not TIMELINE_PATH.exists():
        _timeline_cache = []
        return _timeline_cache
    try:
        data = json.loads(TIMELINE_PATH.read_text())
        events = data.get("events", []) if isinstance(data, dict) else []
        parsed = []
        for e in events:
            d = e.get("date")
            try:
                e["_dt"] = datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=timezone.utc)
                parsed.append(e)
            except (TypeError, ValueError):
                continue
        _timeline_cache = parsed
    except Exception:
        _timeline_cache = []
    return _timeline_cache


def _find_prior_match(series: list[tuple[int, float]], target_odds: float) -> datetime | None:
    """Most recent timestamp where price was within ±ODDS_TOLERANCE of target,
    excluding the past RECENT_EXCLUSION_DAYS. Series is (unix_ts, price), oldest first."""
    if not series:
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(days=RECENT_EXCLUSION_DAYS)
    cutoff_ts = cutoff.timestamp()
    for ts, price in reversed(series):
        if ts >= cutoff_ts:
            continue
        if abs(price - target_odds) <= ODDS_TOLERANCE:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
    return None


def _nearest_timeline_event(target: datetime) -> dict | None:
    """Closest event within ±EVENT_WINDOW_DAYS of target, or None."""
    events = _load_timeline()
    best = None
    best_delta = timedelta(days=EVENT_WINDOW_DAYS + 1)
    for e in events:
        delta = abs(e["_dt"] - target)
        if delta <= timedelta(days=EVENT_WINDOW_DAYS) and delta < best_delta:
            best = e
            best_delta = delta
    if not best:
        return None
    return {
        "date": best["date"],
        "headline": best["headline"],
        "context": best.get("context", ""),
        "tags": best.get("tags", []),
    }


def _gdelt_fallback(target: datetime, question: str) -> dict | None:
    """Live GDELT date-anchored query. Disabled by default for demo safety —
    set ENABLE_GDELT_ECHO_FALLBACK=1 to enable. Throttle and IP-ban risk live
    in fetchers/gdelt.py."""
    if os.getenv("ENABLE_GDELT_ECHO_FALLBACK", "").lower() not in ("1", "true", "yes"):
        return None
    if not hasattr(gdelt, "fetch_around_date"):
        return None
    keywords = " ".join(w for w in question.split() if len(w) > 3)[:120] or "oil OR energy"
    result = gdelt.fetch_around_date(target, keywords, window_days=EVENT_WINDOW_DAYS)
    if not result or not result.get("articles"):
        return None
    top = result["articles"][0]
    return {
        "date": target.strftime("%Y-%m-%d"),
        "headline": top.get("title", "")[:200],
        "context": f"Top GDELT match on that date (domain={top.get('domain', '?')}).",
        "tags": ["gdelt-live"],
    }


def run(state: dict) -> dict:
    pred = state.get("testigo_prediction")
    if not pred or not pred.get("market"):
        return state
    slug = pred["market"]
    poly = (state.get("polymarket_snapshot", {}) or {}).get("markets", {}) or {}
    market = poly.get(slug)
    if not market:
        return state
    token_id = market.get("yes_token_id")
    if not token_id:
        return state

    try:
        current_odds = float(pred.get("current_odds") or market.get("yes_odds") or 0)
    except (TypeError, ValueError):
        return state

    series = polymarket.fetch_lifetime_history(token_id)
    if not series:
        return state

    match_dt = _find_prior_match(series, current_odds)
    if not match_dt:
        return state

    event = _nearest_timeline_event(match_dt)
    source = "timeline"
    if not event:
        event = _gdelt_fallback(match_dt, market.get("question", ""))
        source = "gdelt-fallback"

    if not event:
        # We found a prior-odds match but no event to anchor it. Still useful
        # context for the relator — surface the date without an event narrative.
        state["historical_echo"] = {
            "market": slug,
            "question": market.get("question", ""),
            "current_odds": round(current_odds, 4),
            "matched_date": match_dt.strftime("%Y-%m-%d"),
            "event": None,
            "source": "unmatched",
        }
        return state

    state["historical_echo"] = {
        "market": slug,
        "question": market.get("question", ""),
        "current_odds": round(current_odds, 4),
        "matched_date": match_dt.strftime("%Y-%m-%d"),
        "event": event,
        "source": source,
    }
    return state
