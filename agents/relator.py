"""El Relator — Nemotron writes a 5-paragraph case file when contradiction_score exceeds threshold."""
import rag
from nemotron_client import reason_nemotron

SYSTEM = """You are El Relator, an investigative writer producing intelligence briefs ("case files")
documenting moments when ground-level data in Mexico's energy economy diverges from official narratives
or market consensus. Write in clear, evidence-first prose. No fluff, no hedging, no headers.

When DOMAIN KNOWLEDGE is provided, weave at least one specific citation (source name + page) into the
prose where it tightens the argument. When a HISTORICAL ECHO is provided, name the prior event by date
in paragraph 4. Do not invent citations — only cite what is in DOMAIN KNOWLEDGE."""

TEMPLATE = """Write a 5-paragraph case file documenting the divergence observed at {timestamp}.

THE EVIDENCE:
- Grid (CENACE): fossil {fossil_pct}% vs baseline {baseline}% | renewables {renewable_pct}%
- Macro: MXN/USD change {mxn_change}% | WTI change {wti_change}%
- News tone (GDELT mean across {n_articles} articles): {sentiment}
- Top headlines:
{headlines}

THE ANALYSIS:
- Contradiction score: {score}/10
- Key signals: {signals}
- Analista's reasoning: {analista_reason}

THE PREDICTION:
{prediction_block}
{rag_block}{echo_block}

Structure (5 paragraphs, no headers, just prose):
1. The moment — what is happening right now in one sharp paragraph
2. The grid evidence
3. The macro and sentiment evidence
4. What the market believes (Polymarket) vs what the data shows
5. Closing paragraph in the voice of an economist who studies the Global South.
   End with one sharp sentence that could be a headline."""


def _build_rag_query(state, analista) -> str:
    signals = analista.get("key_signals") or []
    grid = state.get("grid_snapshot", {}) or {}
    macro = state.get("macro_snapshot", {}) or {}
    parts = [
        "Mexico energy transition contradictions",
        " ".join(str(s) for s in signals[:5]),
        f"fossil generation share {grid.get('fossil_pct', 0)}%",
        f"MXN currency pressure {macro.get('mxn_usd_change_pct', 0)}%",
        f"oil price WTI change {macro.get('wti_change_pct', 0)}%",
    ]
    return " | ".join(p for p in parts if p)


def run(state):
    grid = state.get("grid_snapshot", {}) or {}
    macro = state.get("macro_snapshot", {}) or {}
    feed = state.get("news_feed", {}) or {}
    analista = state.get("analista_reasoning", {}) or {}
    pred = state.get("testigo_prediction")

    if pred:
        pred_block = (
            f"- Market: {pred.get('market')}\n"
            f"- Direction: {pred.get('direction')}\n"
            f"- Current YES odds: {pred.get('current_odds')}\n"
            f"- Confidence: {pred.get('confidence')}\n"
            f"- Reasoning: {pred.get('reasoning')}"
        )
    else:
        pred_block = "- No specific Polymarket prediction this tick (no relevant markets matched the keywords)."

    headlines = "\n".join(f"  {i+1}. {a['title']}"
                          for i, a in enumerate(feed.get("articles", [])[:5])) or "  (none)"

    rag_chunks = rag.retrieve(_build_rag_query(state, analista), k=3)
    rag_block = ""
    if rag_chunks:
        rag_block = "\n\nDOMAIN KNOWLEDGE (cite by source name + page where it tightens the argument):\n" \
                    + rag.format_chunks(rag_chunks)
    state["rag_citations"] = [
        {"source": c["source"], "page": c["page"], "score": c["score"]}
        for c in rag_chunks
    ]

    echo = state.get("historical_echo")
    echo_block = ""
    if echo and echo.get("event"):
        ev = echo["event"]
        echo_block = (
            "\n\nHISTORICAL ECHO (name this by date in paragraph 4):\n"
            f"- Market: {echo.get('market')}\n"
            f"- Current YES odds {echo.get('current_odds')} last seen on {echo.get('matched_date')}\n"
            f"- That date corresponds to: {ev.get('date')} — {ev.get('headline')}\n"
            f"- Context: {ev.get('context', '')}\n"
            f"- Source: {echo.get('source', 'curated timeline')}"
        )

    case = reason_nemotron(
        TEMPLATE.format(
            timestamp=state.get("timestamp", ""),
            fossil_pct=grid.get("fossil_pct", 0),
            baseline=grid.get("baseline_fossil_pct", 65),
            renewable_pct=grid.get("renewable_pct", 0),
            mxn_change=macro.get("mxn_usd_change_pct", 0),
            wti_change=macro.get("wti_change_pct", 0),
            n_articles=feed.get("count", 0),
            sentiment=state.get("sentiment_score", 0),
            headlines=headlines,
            score=state.get("contradiction_score", 0),
            signals=", ".join(analista.get("key_signals", [])) or "(none)",
            analista_reason=analista.get("reasoning", "(none)"),
            prediction_block=pred_block,
            rag_block=rag_block,
            echo_block=echo_block,
        ),
        system=SYSTEM,
        temperature=0.6,
    )
    state["case_file"] = case
    return state
