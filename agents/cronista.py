"""El Cronista — aggregates GDELT-precomputed tone into a sentiment score.

Design choice: GDELT already provides per-article tone scores from its own NLP pipeline,
so we skip LLM inference here. Reserves compute for the reasoning agents (Analista, Testigo, Relator).
"""


def run(state):
    feed = state.get("news_feed", {}) or {}
    state["sentiment_score"] = float(feed.get("mean_tone", 0.0))
    return state
