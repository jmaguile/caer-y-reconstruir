"""El Narrador — translates raw data into demo-ready prose.

Three call sites:
  1. summarize_market(question, history, current_price) — one paragraph
     for the top of the selected-market view: what the market is about,
     what its lifetime range means, what the current price implies.
  2. summarize_loop(ticks) — one paragraph for above the loop activity
     table: what the recent contradiction scores + grid fossil % + mean
     tone are telling an economist about Mexico's energy state right now.
  3. summarize_price_journey(question, history) — one paragraph for the
     price-level history panel: replaces the band-by-band date table
     with a story about how price moved across regimes over the curve's
     lifetime.

All three call Nemotron once with low temperature for grounded prose.
Dashboard caches results per stable input so each market/loop snapshot
pays for narration only once per 15 minutes.
"""
from __future__ import annotations

from nemotron_client import reason_nemotron


def _safe(s: str, n: int) -> str:
    s = (s or "").strip().replace("\n", " ")
    return s[:n]


# ----- 1. Market briefing -----

_MARKET_SYSTEM = (
    "You are El Narrador, a senior macroeconomist briefing an audience on a "
    "prediction market. Two short paragraphs (3-4 sentences each), no preamble, "
    "no markdown headers, no bullet points. Plain prose only.\n\n"
    "First paragraph: what the market is asking and what the current YES odds "
    "imply in plain probability language (e.g. '~1-in-3 chance', 'near-certain', "
    "'long-shot'). Second paragraph: what the lifetime price journey reveals "
    "about how the market repriced over time, in language a Latin American "
    "policy analyst would find useful."
)

_MARKET_PROMPT = """Market question: {question}
Current YES odds: {current_price:.3f}
Lifetime range: {lo:.3f} (low, on {lo_date}) to {hi:.3f} (high, on {hi_date})
First quoted: {first_date} at {first_price:.3f}
Last quoted: {last_date} at {last_price:.3f}

Brief the audience."""


def summarize_market(question: str, history: list[tuple[int, float]],
                     current_price: float) -> str:
    if not history:
        return ("No lifetime price history available for this market, so a "
                "price-journey briefing isn't possible. Current YES odds are "
                f"{current_price:.3f}.")
    import pandas as pd
    df = pd.DataFrame(history, columns=["ts", "price"])
    df["date"] = pd.to_datetime(df["ts"], unit="s", utc=True)
    df = df.sort_values("date").reset_index(drop=True)
    lo_row = df.loc[df["price"].idxmin()]
    hi_row = df.loc[df["price"].idxmax()]
    try:
        out = reason_nemotron(
            _MARKET_PROMPT.format(
                question=_safe(question, 200),
                current_price=current_price,
                lo=float(lo_row["price"]), lo_date=lo_row["date"].date(),
                hi=float(hi_row["price"]), hi_date=hi_row["date"].date(),
                first_date=df.iloc[0]["date"].date(),
                first_price=float(df.iloc[0]["price"]),
                last_date=df.iloc[-1]["date"].date(),
                last_price=float(df.iloc[-1]["price"]),
            ),
            system=_MARKET_SYSTEM,
            temperature=0.3,
        )
        return out.strip() or "(narrator returned empty)"
    except Exception as e:
        return f"(narrator failed: {type(e).__name__})"


# ----- 2. Loop activity summary -----

_LOOP_SYSTEM = (
    "You are El Narrador, briefing on the autonomous monitoring loop's most "
    "recent ticks. ONE paragraph, 2-3 sentences, no preamble. Read the "
    "contradiction score (0-10 scale, higher = bigger gap between official "
    "narrative and ground-level data), the grid fossil percentage (vs ~65% "
    "baseline), and the news mean tone (-10 to +10, negative = hostile "
    "coverage), and tell the audience in plain English what's happening to "
    "Mexico's energy economy right now."
)

_LOOP_PROMPT = """Last {n} loop ticks (newest last):
{rows}

Brief the audience on the current state."""


def summarize_loop(ticks: list[dict]) -> str:
    if not ticks:
        return ("The loop hasn't logged any ticks yet — start the autonomous "
                "monitor with `python run.py` to populate this panel.")
    recent = ticks[-12:]
    rows = []
    for t in recent:
        rows.append(
            f"  - {t.get('logged_at', '?')[:19]}  "
            f"contradiction={t.get('contradiction_score', '?')}  "
            f"fossil={t.get('fossil_pct', '?')}%  "
            f"tone={t.get('mean_tone', '?')}"
        )
    try:
        out = reason_nemotron(
            _LOOP_PROMPT.format(n=len(recent), rows="\n".join(rows)),
            system=_LOOP_SYSTEM,
            temperature=0.3,
        )
        return out.strip() or "(narrator returned empty)"
    except Exception as e:
        return f"(narrator failed: {type(e).__name__})"


# ----- 3. Price journey narrative (replaces the band-by-band table) -----

_JOURNEY_SYSTEM = (
    "You are El Narrador. Read the price trajectory of a prediction market "
    "and write ONE paragraph (3-4 sentences) about how the market traveled "
    "between conviction regimes — long-shot (<20¢), uncertain (20-60¢), "
    "high-conviction (>60¢). No preamble. No markdown. Refer to specific "
    "dates and price levels. Make it useful to a Latin American policy "
    "analyst trying to understand the global story."
)

_JOURNEY_PROMPT = """Market question: {question}

Price waypoints across the lifetime curve:
{waypoints}

Tell the price journey."""


def summarize_price_journey(question: str,
                            history: list[tuple[int, float]]) -> str:
    if not history or len(history) < 10:
        return "Not enough price history yet to narrate a journey."
    import pandas as pd
    df = pd.DataFrame(history, columns=["ts", "price"])
    df["date"] = pd.to_datetime(df["ts"], unit="s", utc=True)
    df = df.sort_values("date").reset_index(drop=True)
    # Sample ~8 evenly-spaced waypoints so the prompt isn't huge
    n = len(df)
    step = max(1, n // 8)
    sample = df.iloc[list(range(0, n, step))[:8] + [n - 1]].drop_duplicates("date")
    waypoints = "\n".join(
        f"  - {row['date'].date()}: {row['price']:.3f}"
        for _, row in sample.iterrows()
    )
    try:
        out = reason_nemotron(
            _JOURNEY_PROMPT.format(question=_safe(question, 200),
                                   waypoints=waypoints),
            system=_JOURNEY_SYSTEM,
            temperature=0.3,
        )
        return out.strip() or "(narrator returned empty)"
    except Exception as e:
        return f"(narrator failed: {type(e).__name__})"
