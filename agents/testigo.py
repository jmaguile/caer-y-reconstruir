"""El Testigo — Nemotron picks the Polymarket market most divergent from ground data."""
from nemotron_client import reason_nemotron, parse_json_blob

SYSTEM = """You are El Testigo. You witness the ground-level data, compare it to current Polymarket odds,
and predict where market odds SHOULD move if they priced in what you observe. You are not a trader;
you are a witness producing testimony.

Reply with valid JSON ONLY."""

PROMPT = """Ground state:
- Fossil share of Mexico's grid: {fossil_pct}% (baseline {baseline}%)
- MXN/USD change: {mxn_change}%
- WTI change: {wti_change}%
- News sentiment (GDELT mean tone): {sentiment}
- Analista's reasoning: {analista}

Current Polymarket markets and YES odds:
{markets}

Choose ONE market whose current odds most clearly diverge from the ground data.
Reply with EXACTLY this JSON shape, nothing else:
{{
  "market": "<exact slug from list above, or empty string if none relevant>",
  "direction": "YES odds should increase" or "YES odds should decrease",
  "current_odds": <float from list>,
  "confidence": <float 0-1>,
  "reasoning": "<one sentence connecting ground data to the predicted move>"
}}"""


def run(state):
    poly_markets = (state.get("polymarket_snapshot", {}) or {}).get("markets", {}) or {}
    if not poly_markets:
        state["testigo_prediction"] = None
        return state

    markets_str = "\n".join(
        f"  - {slug}: yes={data['yes_odds']:.3f} | {data['question']}"
        for slug, data in list(poly_markets.items())[:15]
    )

    grid = state.get("grid_snapshot", {}) or {}
    macro = state.get("macro_snapshot", {}) or {}
    raw = reason_nemotron(
        PROMPT.format(
            fossil_pct=grid.get("fossil_pct", 0),
            baseline=grid.get("baseline_fossil_pct", 65),
            mxn_change=macro.get("mxn_usd_change_pct", 0),
            wti_change=macro.get("wti_change_pct", 0),
            sentiment=state.get("sentiment_score", 0),
            analista=(state.get("analista_reasoning", {}) or {}).get("reasoning", "(none)"),
            markets=markets_str,
        ),
        system=SYSTEM,
    )
    parsed = parse_json_blob(raw)
    if parsed.get("market") and parsed["market"] in poly_markets:
        # Override current_odds with the real number to prevent hallucination
        parsed["current_odds"] = poly_markets[parsed["market"]]["yes_odds"]
        state["testigo_prediction"] = parsed
    else:
        state["testigo_prediction"] = None
    return state
