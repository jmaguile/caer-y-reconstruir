"""La Memoria — persistent JSONL log of predictions and tick history.

Survives restarts. Each prediction is re-scored against current Polymarket odds on every tick
to produce verdicts ('market caught up', 'gap persists', 'we were wrong').
"""
import json
from pathlib import Path
from datetime import datetime

MEM_DIR = Path(__file__).resolve().parent / "memory"
MEM_DIR.mkdir(exist_ok=True)
PRED_LOG = MEM_DIR / "predictions.jsonl"
TICK_LOG = MEM_DIR / "ticks.jsonl"


def _append(path: Path, entry: dict) -> None:
    entry = {**entry, "logged_at": datetime.utcnow().isoformat()}
    with path.open("a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def log_prediction(entry: dict) -> None:
    _append(PRED_LOG, entry)


def log_tick(entry: dict) -> None:
    _append(TICK_LOG, entry)


def load_predictions() -> list[dict]:
    if not PRED_LOG.exists():
        return []
    return [json.loads(line) for line in PRED_LOG.read_text().splitlines() if line.strip()]


def update_verdicts(current_polymarket: dict[str, float]) -> int:
    """Re-score open predictions against current odds. Append verdict entries; return count updated."""
    history = load_predictions()
    open_preds = [e for e in history if not e.get("verdict")]
    updated = 0
    for entry in open_preds:
        pred = entry.get("our_prediction") or {}
        market = pred.get("market")
        if not market or market not in current_polymarket:
            continue
        odds_then = float(entry.get("polymarket_odds_at_prediction") or 0)
        odds_now = float(current_polymarket[market])
        gap = odds_now - odds_then
        direction_up = "increase" in str(pred.get("direction", "")).lower()

        if abs(gap) >= 0.05 and (gap > 0) == direction_up:
            verdict = "market caught up"
        elif abs(gap) < 0.02:
            verdict = "gap persists"
        else:
            verdict = "we were wrong"

        _append(PRED_LOG, {
            **entry,
            "polymarket_odds_now": odds_now,
            "gap": round(gap, 4),
            "verdict": verdict,
        })
        updated += 1
    return updated
