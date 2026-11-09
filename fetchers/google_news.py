"""Google News RSS — no-auth news search with date-window filtering.

Used as a fallback after GDELT in agents/inspector.py. GDELT throttles
aggressively (30s/call, 10-min IP ban on trip) and its English-source
coverage is patchy on long-tail dates. Google News RSS has neither
limitation — no auth, no enforced rate limit, mainstream-source bias.

Query format supports `after:YYYY-MM-DD before:YYYY-MM-DD` so we can
anchor a search to a ±N-day window around an anomaly date, the same
shape inspector.interpret_anomaly already uses for GDELT.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

import httpx

URL = "https://news.google.com/rss/search"
HEADERS = {"User-Agent": "caer-y-reconstruir/1.0 (Mexico energy monitor; research)"}


def _domain_of(url: str, fallback: str = "news.google.com") -> str:
    m = re.match(r"https?://([^/]+)", url or "")
    if not m:
        return fallback
    host = m.group(1).lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def fetch_around_date(target_dt: datetime, keywords: str,
                      window_days: int = 7, limit: int = 10) -> dict:
    """RSS-driven news search around a target date. Returns the same shape
    as gdelt.fetch_around_date so the inspector can swap them transparently.

    Returns {"articles": [...], "count": N, "source": "google_news",
             "window": "<start> → <end>"} on success.
    Returns {"articles": [], "count": 0, "error": "..."} on failure.
    """
    if target_dt.tzinfo is None:
        target_dt = target_dt.replace(tzinfo=timezone.utc)
    start = (target_dt - timedelta(days=window_days)).date()
    end = (target_dt + timedelta(days=window_days)).date()
    # Google's `after:` / `before:` in search queries: works in RSS too.
    query = f"{keywords} after:{start.isoformat()} before:{end.isoformat()}"
    params = {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}
    try:
        r = httpx.get(URL, params=params, headers=HEADERS, timeout=15.0)
        r.raise_for_status()
        root = ET.fromstring(r.text)
        items = root.findall(".//item")
        articles = []
        for it in items[:limit]:
            title = (it.findtext("title") or "").strip()[:200]
            link = (it.findtext("link") or "").strip()
            pub_date = (it.findtext("pubDate") or "").strip()
            source_el = it.find("source")
            domain = ""
            if source_el is not None:
                domain = (source_el.text or "").strip().lower()
            if not domain:
                domain = _domain_of(link)
            articles.append({
                "title": title,
                "url": link,
                "domain": domain,
                "seendate": pub_date,
            })
        return {"articles": articles, "count": len(articles),
                "source": "google_news",
                "window": f"{start} → {end}"}
    except Exception as e:
        return {"articles": [], "count": 0,
                "error": (type(e).__name__ + ": " + str(e))[:120],
                "source": "google_news"}


def fetch_recent(keywords: str, days: int = 7, limit: int = 15) -> dict:
    """Recent news, no specific anchor date — used for the observador feed.
    `when:Xd` is Google's relative-time operator (X up to 365)."""
    query = f"{keywords} when:{days}d"
    params = {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}
    try:
        r = httpx.get(URL, params=params, headers=HEADERS, timeout=15.0)
        r.raise_for_status()
        root = ET.fromstring(r.text)
        items = root.findall(".//item")
        articles = []
        for it in items[:limit]:
            title = (it.findtext("title") or "").strip()[:200]
            link = (it.findtext("link") or "").strip()
            pub_date = (it.findtext("pubDate") or "").strip()
            source_el = it.find("source")
            domain = (source_el.text or "").strip().lower() if source_el is not None else ""
            if not domain:
                domain = _domain_of(link)
            articles.append({
                "title": title,
                "url": link,
                "domain": domain,
                "seendate": pub_date,
            })
        return {"articles": articles, "count": len(articles),
                "source": "google_news"}
    except Exception as e:
        return {"articles": [], "count": 0,
                "error": (type(e).__name__ + ": " + str(e))[:120],
                "source": "google_news"}


if __name__ == "__main__":
    import json
    from datetime import datetime
    out = fetch_around_date(datetime(2026, 3, 17, tzinfo=timezone.utc),
                            "Trump Iran tensions", window_days=7, limit=5)
    print(json.dumps(out, indent=2))
