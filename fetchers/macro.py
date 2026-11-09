"""MXN/USD and WTI crude via yfinance. Reliable, no API key."""
import yfinance as yf


def _safe_pull(symbol: str, key: str) -> dict:
    try:
        hist = yf.Ticker(symbol).history(period="2d", interval="1h")
        if hist.empty:
            return {f"{key}_error": "empty history"}
        last = float(hist["Close"].iloc[-1])
        first = float(hist["Close"].iloc[0])
        change_pct = 100 * (last - first) / first if first else 0.0
        return {key: round(last, 6), f"{key}_change_pct": round(change_pct, 3)}
    except Exception as e:
        return {f"{key}_error": (type(e).__name__ + ": " + str(e))[:120]}


def fetch() -> dict:
    snap: dict = {}
    snap.update(_safe_pull("MXN=X", "mxn_usd"))   # USD->MXN; yfinance gives MXN per USD here
    snap.update(_safe_pull("CL=F", "wti"))         # WTI crude futures
    snap.update(_safe_pull("BZ=F", "brent"))       # Brent crude (bonus signal)
    return snap


if __name__ == "__main__":
    import json
    print(json.dumps(fetch(), indent=2))
