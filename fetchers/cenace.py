"""Mexico grid breakdown via Electricity Maps API (zone MX).

The original CENACE endpoint (ws.cenace.gob.mx) was retired; this module
keeps the name `cenace` so loop.py and the agents don't need to change.
Electricity Maps aggregates CENACE feeds plus other operators and exposes
real-time power consumption breakdown by source.
"""
import os
import httpx
from dotenv import load_dotenv

load_dotenv()

ELECTRICITYMAP_URL = "https://api.electricitymap.org/v3/power-breakdown/latest"
ZONE = "MX"
BASELINE_FOSSIL_PCT = 65.0  # 2024 SENER reference for contradiction scoring


def fetch() -> dict:
    token = os.environ.get("ELECTRICITYMAP_TOKEN")
    if not token:
        return _unavailable("ELECTRICITYMAP_TOKEN not set in .env")
    try:
        r = httpx.get(
            ELECTRICITYMAP_URL,
            params={"zone": ZONE},
            headers={"auth-token": token},
            timeout=15.0,
        )
        r.raise_for_status()
        data = r.json()
        breakdown = data.get("powerConsumptionBreakdown") or {}
        renewable_pct = float(data.get("renewablePercentage") or 0)
        fossil_free_pct = float(data.get("fossilFreePercentage") or 0)
        fossil_pct = round(100.0 - fossil_free_pct, 2)
        return {
            "source": "electricitymaps_live",
            "datetime": data.get("datetime", ""),
            "renewable_pct": round(renewable_pct, 2),
            "fossil_pct": fossil_pct,
            "fossil_free_pct": round(fossil_free_pct, 2),
            "baseline_fossil_pct": BASELINE_FOSSIL_PCT,
            "breakdown_mw": {k: int(v) for k, v in breakdown.items() if v},
            "total_mw": int(data.get("powerConsumptionTotal", 0) or 0),
        }
    except Exception as e:
        return _unavailable(f"{type(e).__name__}: {str(e)[:80]}")


def _unavailable(reason: str) -> dict:
    return {
        "source": f"unavailable ({reason[:80]})",
        "renewable_pct": round(100 - BASELINE_FOSSIL_PCT, 2),
        "fossil_pct": BASELINE_FOSSIL_PCT,
        "baseline_fossil_pct": BASELINE_FOSSIL_PCT,
        "breakdown_mw": {},
    }


if __name__ == "__main__":
    import json
    print(json.dumps(fetch(), indent=2))
