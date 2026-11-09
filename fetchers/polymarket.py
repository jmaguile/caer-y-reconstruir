"""Polymarket — prediction market odds + 7-day price history. Public APIs, no auth needed."""
import time
import httpx
import json as _json
import re

POLY_URL = "https://gamma-api.polymarket.com/markets"
CLOB_HISTORY_URL = "https://clob.polymarket.com/prices-history"

# Global-economist beat. Broader than the prior Mexico-leaning list: any market
# an economist would actually have a view on. The LatAm interpretive layer is
# applied by the user on stage, not filtered in code.
RELEVANT_KEYWORDS = [
    # Energy & commodities
    "oil", "petrole", "wti", "brent", "opec", "crude", "gasoline",
    "energy", "energia", "gas", "lng", "electric", "pemex", "cfe",
    "renewable", "solar", "wind", "fossil", "climate", "carbon",
    "gold", "silver", "copper", "lithium", "metal", "commodities",
    "wheat", "grain", "agriculture",
    # Central banks & rates (global)
    "fed-", "fed", "ecb", "boe", "boj", "pboc",
    "rate-cut", "rate-hike", "interest-rate", "interest", "rates",
    "treasury", "yield", "bond", "bonds",
    # Inflation / growth / recession
    "inflation", "deflation", "cpi", "ppi", "recession", "gdp",
    "unemployment", "jobs", "payrolls",
    # FX & currency (no longer LatAm-only)
    "currency", "dollar", "euro", "yen", "yuan", "peso",
    "mxn", "brl", "ars", "usd-mxn", "usd-cny", "eur-usd",
    # Geopolitical & trade
    "sanctions", "tariff", "trade-war", "trade", "china", "russia",
    "ukraine", "iran", "israel", "saudi", "opec+",
    # Mexico & LatAm (kept — still relevant, not exclusive)
    "amlo", "sheinbaum", "morena", "mexico", "mexican", "mexicano",
    "lula", "milei", "brazil", "argentina", "chile", "colombia",
    "venezuela", "latin", "latam",
    # Crypto-as-macro-signal (BTC tracks risk appetite + rate expectations)
    "bitcoin", "btc", "ethereum", "crypto",
    # Politics with direct market impact
    "election", "biden", "trump", "harris", "putin", "xi-",
]

EXCLUDE_KEYWORDS = ["fifa", "world-cup", "soccer", "football",
                    "olympic", "nfl", "nba", "nhl", "mlb", "stanley",
                    "hockey", "basketball", "baseball", "champions",
                    "super-bowl", "premier-league", "taylor", "swift",
                    "oscar", "grammy", "emmy", "marvel"]

_RELEVANT_RE = re.compile(r"\b(?:" + "|".join(re.escape(k) for k in RELEVANT_KEYWORDS) + r")\b",
                          re.IGNORECASE)
_EXCLUDE_RE = re.compile(r"\b(?:" + "|".join(re.escape(k) for k in EXCLUDE_KEYWORDS) + r")\b",
                         re.IGNORECASE)


def _match_keywords(text: str) -> list[str]:
    """All relevant keywords that hit. Returns [] if any exclusion matches."""
    if _EXCLUDE_RE.search(text):
        return []
    hits = _RELEVANT_RE.findall(text)
    # Dedupe while preserving order, cap to 3 most distinctive ones for display
    seen = []
    for h in hits:
        h_low = h.lower().rstrip("-")
        if h_low not in seen:
            seen.append(h_low)
    return seen[:3]

# Cap how many markets we pull 7-day history for, per tick. Each adds one
# CLOB API call, so we sort by volume and take the top N. History is cached
# for 5 minutes since it changes slowly.
TOP_N_HISTORY = 15
HISTORY_CACHE_TTL_SEC = 300
_history_cache: dict[str, tuple[float, dict]] = {}

# Lifetime history is only pulled on-demand for the single market the testigo
# picks, so it's one extra CLOB call per tick at most. Cache for 24h since
# the older portion of the curve barely changes once it's history.
LIFETIME_CACHE_TTL_SEC = 86400
_lifetime_cache: dict[str, tuple[float, list]] = {}


def _yes_token_id(market: dict) -> str:
    """Polymarket returns clobTokenIds as a JSON-string list [yes_id, no_id]."""
    raw = market.get("clobTokenIds")
    if isinstance(raw, str):
        try:
            raw = _json.loads(raw)
        except _json.JSONDecodeError:
            return ""
    if isinstance(raw, list) and raw:
        return str(raw[0])
    return ""


def _fetch_price_history(token_id: str) -> dict:
    """7-day price trajectory for one market. Cached for 5 min."""
    if not token_id:
        return {}
    now = time.time()
    cached = _history_cache.get(token_id)
    if cached and (now - cached[0]) < HISTORY_CACHE_TTL_SEC:
        return cached[1]
    try:
        r = httpx.get(
            CLOB_HISTORY_URL,
            params={"market": token_id, "interval": "1w", "fidelity": 60},
            timeout=10.0,
        )
        r.raise_for_status()
        points = r.json().get("history", [])
        if not points:
            return {}
        prices = [float(p.get("p", 0)) for p in points]
        first, last = prices[0], prices[-1]
        change = round(last - first, 4)
        peak, trough = max(prices), min(prices)
        result = {
            "points": [(int(p.get("t", 0)), round(float(p.get("p", 0)), 4)) for p in points],
            "price_7d_ago": round(first, 4),
            "price_now": round(last, 4),
            "change_7d": change,
            "peak_7d": round(peak, 4),
            "trough_7d": round(trough, 4),
            "range_7d": round(peak - trough, 4),
        }
        _history_cache[token_id] = (now, result)
        return result
    except Exception as e:
        return {"error": (type(e).__name__ + ": " + str(e))[:80]}


def fetch_lifetime_history(token_id: str) -> list[tuple[int, float]]:
    """Full-lifetime daily price curve for one market. Returns list of (unix_ts, price).
    Cached 24h. Used by event_matcher to find when current odds were last seen historically.
    """
    if not token_id:
        return []
    now = time.time()
    cached = _lifetime_cache.get(token_id)
    if cached and (now - cached[0]) < LIFETIME_CACHE_TTL_SEC:
        return cached[1]
    try:
        r = httpx.get(
            CLOB_HISTORY_URL,
            params={"market": token_id, "interval": "max", "fidelity": 1440},
            timeout=15.0,
        )
        r.raise_for_status()
        points = r.json().get("history", []) or []
        series = [(int(p.get("t", 0)), round(float(p.get("p", 0)), 4)) for p in points]
        _lifetime_cache[token_id] = (now, series)
        return series
    except Exception:
        return []


def _to_float(x) -> float:
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, str):
        try:
            return float(x)
        except ValueError:
            return 0.0
    return 0.0


def _yes_odds(market: dict) -> float:
    """Extract YES outcome price. Polymarket returns outcomePrices as a JSON-string list."""
    prices = market.get("outcomePrices")
    if isinstance(prices, str):
        try:
            prices = _json.loads(prices)
        except _json.JSONDecodeError:
            prices = []
    if isinstance(prices, list) and prices:
        return _to_float(prices[0])
    return 0.0


def fetch(limit: int = 500, page_size: int = 100) -> dict:
    """Paginate through Polymarket markets and filter for energy/Mexico relevance.
    Gamma API caps each request at 100 markets, so we walk offsets."""
    markets = []
    try:
        for offset in range(0, limit, page_size):
            r = httpx.get(
                POLY_URL,
                params={
                    "closed": "false",
                    "active": "true",
                    "limit": page_size,
                    "offset": offset,
                    "order": "volume24hr",
                    "ascending": "false",
                },
                timeout=15.0,
            )
            r.raise_for_status()
            body = r.json()
            batch = body if isinstance(body, list) else body.get("data", [])
            if not batch:
                break
            markets.extend(batch)
            if len(batch) < page_size:
                break

        relevant = {}
        for m in markets:
            slug = m.get("slug") or m.get("conditionId") or ""
            question = m.get("question") or ""
            # Run match against both slug and question; merge dedup'd keywords
            keywords = _match_keywords(question) or _match_keywords(slug)
            if not keywords:
                continue
            relevant[slug] = {
                "question": question[:120],
                "yes_odds": round(_yes_odds(m), 4),
                "volume": _to_float(m.get("volume", 0)),
                "yes_token_id": _yes_token_id(m),
                "match_keywords": keywords,
            }

        # Attach 7-day price history to the top-N most liquid markets.
        # Sorting by volume keeps history calls focused on markets that
        # actually move and matter for the agents' reasoning.
        top_slugs = sorted(relevant, key=lambda s: relevant[s]["volume"], reverse=True)[:TOP_N_HISTORY]
        for slug in top_slugs:
            history = _fetch_price_history(relevant[slug]["yes_token_id"])
            if history:
                relevant[slug]["history_7d"] = history

        return {"markets": relevant, "count": len(relevant),
                "scanned": len(markets), "with_history": len(top_slugs)}
    except Exception as e:
        return {"markets": {}, "count": 0, "scanned": len(markets),
                "error": (type(e).__name__ + ": " + str(e))[:120]}


def slug_to_odds(snapshot: dict) -> dict[str, float]:
    """Flat slug->yes_odds map for memory.update_verdicts."""
    return {slug: data["yes_odds"] for slug, data in snapshot.get("markets", {}).items()}


if __name__ == "__main__":
    import json
    print(json.dumps(fetch(), indent=2))
