"""GDELT — global news article list. GDELT's artlist mode returns metadata only
(no tone), so we compute sentiment locally with VADER on English titles.

Real sentiment from real article titles. Non-English articles are returned
with tone=None and excluded from the mean."""
import time
import httpx
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
HEADERS = {"User-Agent": "caer-y-reconstruir/1.0 (Mexico energy monitor; research)"}
_vader = SentimentIntensityAnalyzer()

# GDELT doesn't expose rate-limit headers and locks the IP for ~10 min
# once tripped. Hard cap one call per 30s regardless of loop cadence.
MIN_INTERVAL_SEC = 30.0
_last_call_ts = 0.0
_last_result: dict | None = None

# Global economic news beat — broader than the original Pemex/CFE narrow
# query so the loop's news feed reflects what an economist would actually
# read (Fed policy, energy markets, currency stress, trade frictions,
# central-bank moves), with Mexico-specific terms still included so the
# LatAm interpretive layer the user provides on stage still has anchors.
DEFAULT_QUERY = ("oil OR Fed OR inflation OR sanctions OR tariff OR "
                 "\"central bank\" OR \"interest rate\" OR \"supply chain\" OR "
                 "Pemex OR CFE OR peso")


def fetch(query: str = DEFAULT_QUERY, limit: int = 15) -> dict:
    global _last_call_ts, _last_result
    elapsed = time.time() - _last_call_ts
    if elapsed < MIN_INTERVAL_SEC and _last_result is not None:
        cached = dict(_last_result)
        cached["cached"] = True
        cached["age_sec"] = round(elapsed, 1)
        return cached
    _last_call_ts = time.time()
    params = {
        "query": query,
        "mode": "artlist",
        "format": "json",
        "maxrecords": limit,
        "sort": "datedesc",
        "timespan": "7d",
    }
    try:
        r = httpx.get(GDELT_URL, params=params, headers=HEADERS, timeout=30.0)
        r.raise_for_status()
        try:
            data = r.json()
        except Exception:
            return {"articles": [], "mean_tone": 0.0, "count": 0,
                    "error": f"non-json response: {r.text[:120]!r}"}
        articles = data.get("articles", []) if isinstance(data, dict) else []
        items = []
        tones = []
        for a in articles:
            title = (a.get("title") or "")[:200]
            lang = (a.get("language") or "").lower()
            if lang in ("english", "eng"):
                # VADER compound is [-1, 1]; scale to GDELT-like [-10, 10].
                tone = round(_vader.polarity_scores(title)["compound"] * 10, 3)
                tones.append(tone)
            else:
                tone = None
            items.append({
                "title": title,
                "url": a.get("url", ""),
                "tone": tone,
                "lang": lang,
                "domain": a.get("domain", ""),
                "seendate": a.get("seendate", ""),
            })
        mean_tone = round(sum(tones) / len(tones), 3) if tones else 0.0
        result = {"articles": items, "mean_tone": mean_tone, "count": len(items),
                  "tone_computed_on": len(tones)}
        _last_result = result
        return result
    except Exception as e:
        return {"articles": [], "mean_tone": 0.0, "count": 0,
                "error": (type(e).__name__ + ": " + str(e))[:120]}


def fetch_around_date(target, keywords: str, window_days: int = 3, limit: int = 5) -> dict:
    """Date-anchored GDELT artlist. `target` is a datetime; query a ±window_days span around it.
    Respects the same MIN_INTERVAL throttle as fetch() — returns {"throttled": True}
    with cooldown_remaining seconds if the throttle is hot, so callers can distinguish
    'GDELT rate-limited' from 'GDELT returned no articles' (very different UX states)."""
    global _last_call_ts
    from datetime import timedelta
    elapsed = time.time() - _last_call_ts
    if elapsed < MIN_INTERVAL_SEC:
        return {"throttled": True,
                "cooldown_remaining": round(MIN_INTERVAL_SEC - elapsed, 1)}
    _last_call_ts = time.time()
    start = target - timedelta(days=window_days)
    end = target + timedelta(days=window_days)
    params = {
        "query": keywords,
        "mode": "artlist",
        "format": "json",
        "maxrecords": limit,
        "sort": "tonedesc",
        "startdatetime": start.strftime("%Y%m%d%H%M%S"),
        "enddatetime": end.strftime("%Y%m%d%H%M%S"),
    }
    try:
        r = httpx.get(GDELT_URL, params=params, headers=HEADERS, timeout=30.0)
        r.raise_for_status()
        try:
            data = r.json()
        except Exception:
            return {}
        articles = data.get("articles", []) if isinstance(data, dict) else []
        items = [{
            "title": (a.get("title") or "")[:200],
            "url": a.get("url", ""),
            "domain": a.get("domain", ""),
            "seendate": a.get("seendate", ""),
        } for a in articles]
        return {"articles": items, "count": len(items),
                "window": f"{start.date()} → {end.date()}"}
    except Exception:
        return {}


if __name__ == "__main__":
    import json
    print(json.dumps(fetch(), indent=2))
