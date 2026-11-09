"""El Observador — pulls data from all sources into the shared state. No LLM."""
from datetime import datetime
from fetchers import cenace, macro, gdelt, polymarket


def run(state):
    state["timestamp"] = datetime.utcnow().isoformat()
    state["grid_snapshot"] = cenace.fetch()
    state["macro_snapshot"] = macro.fetch()
    state["news_feed"] = gdelt.fetch()
    state["polymarket_snapshot"] = polymarket.fetch()
    state["tick_count"] = state.get("tick_count", 0) + 1
    return state
