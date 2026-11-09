"""El Analista — Nemotron computes a contradiction_score across grid, macro, and news signals."""
import rag
from nemotron_client import reason_nemotron, parse_json_blob

SYSTEM = """You are El Analista, a global macroeconomist watching for contradictions
between official narratives and ground-level data. Your beat is broad — energy
transitions, central bank policy, currency stress, commodity flows, and political
risk across major economies — with particular sensitivity to how global signals
will reverberate in Latin America and Mexico's energy economy specifically.

Score whatever signals the current snapshot actually surfaces. If the grid data
is the dominant signal, score against the stated transition. If the dominant
signal is Fed policy / oil / FX / political risk, score against the official
narrative of stability and orderly transition that those signals threaten.

Output a contradiction_score between 0 and 10:
  0 = data fully consistent with prevailing official narratives
  5 = ambiguous or mixed signals
 10 = strong divergence between what officials claim and what data shows

Reply with valid JSON ONLY, no prose, no markdown fences."""

PROMPT = """Snapshot at {timestamp}:

Grid (CENACE, source={grid_source}):
- Renewable share: {renewable_pct}%
- Fossil share: {fossil_pct}% (baseline {baseline}%)

Macro:
- MXN per USD: {mxn_usd} (change {mxn_change}%)
- WTI: ${wti} (change {wti_change}%)

News (GDELT, {n_articles} articles):
- Mean tone (negative = hostile coverage): {sentiment}
- Top 3 headlines:
{headlines}
{rag_context}
Compute the contradiction_score. Reply with this exact JSON shape, nothing else:
{{
  "contradiction_score": <float 0-10>,
  "key_signals": ["<signal1>", "<signal2>", "<signal3>"],
  "reasoning": "<one sentence explaining the score>"
}}"""


def run(state):
    grid = state.get("grid_snapshot", {}) or {}
    macro = state.get("macro_snapshot", {}) or {}
    feed = state.get("news_feed", {}) or {}
    headlines = "\n".join(f"  {i+1}. {a['title']}"
                          for i, a in enumerate(feed.get("articles", [])[:3])) or "  (none)"

    rag_query = (
        f"Mexico energy transition fossil share {grid.get('fossil_pct', 0)}% "
        f"MXN currency {macro.get('mxn_usd_change_pct', 0)}%"
    )
    rag_chunks = rag.retrieve(rag_query, k=1)
    rag_context = ""
    if rag_chunks:
        rag_context = "\nDomain context (for grounding only, not a signal):\n" + rag.format_chunks(rag_chunks)

    raw = reason_nemotron(
        PROMPT.format(
            timestamp=state.get("timestamp", ""),
            grid_source=grid.get("source", "unknown"),
            renewable_pct=grid.get("renewable_pct", 0),
            fossil_pct=grid.get("fossil_pct", 0),
            baseline=grid.get("baseline_fossil_pct", 65),
            mxn_usd=macro.get("mxn_usd", "n/a"),
            mxn_change=macro.get("mxn_usd_change_pct", 0),
            wti=macro.get("wti", "n/a"),
            wti_change=macro.get("wti_change_pct", 0),
            n_articles=feed.get("count", 0),
            sentiment=state.get("sentiment_score", 0),
            headlines=headlines,
            rag_context=rag_context,
        ),
        system=SYSTEM,
    )
    parsed = parse_json_blob(raw)
    try:
        score = float(parsed.get("contradiction_score", 0))
    except (TypeError, ValueError):
        score = 0.0
    state["contradiction_score"] = max(0.0, min(10.0, score))
    state["analista_reasoning"] = parsed
    return state
