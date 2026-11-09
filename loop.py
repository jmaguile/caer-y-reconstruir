"""60-second autonomous loop. Coordinates the 5 agents and triggers Relator on threshold breach."""
import os
import time
import traceback
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from state import empty_state
from agents import observador, cronista, analista, testigo, relator
from fetchers import polymarket
from memory import log_prediction, log_tick, update_verdicts, load_predictions
import event_matcher

load_dotenv()
THRESHOLD = float(os.getenv("CONTRADICTION_THRESHOLD", "6.0"))
INTERVAL = int(os.getenv("TICK_INTERVAL_SECONDS", "60"))

console = Console()

# Unicode block characters for in-terminal sparklines. Eight levels of
# height map prices onto vertical bars without needing matplotlib.
_BARS = "▁▂▃▄▅▆▇█"


def _sparkline(prices: list[float]) -> str:
    if not prices:
        return ""
    lo, hi = min(prices), max(prices)
    rng = (hi - lo) or 1.0
    return "".join(_BARS[min(7, int((p - lo) / rng * 7))] for p in prices)


def _poly_sparklines(poly: dict, top_n: int = 5) -> list[str]:
    """One line per top market: question, current odds, 7d sparkline, 7d change."""
    markets = poly.get("markets", {}) or {}
    have_history = [(slug, m) for slug, m in markets.items() if m.get("history_7d")]
    have_history.sort(key=lambda kv: kv[1].get("volume", 0), reverse=True)
    lines = []
    for slug, m in have_history[:top_n]:
        h = m["history_7d"]
        prices = [p for _, p in h.get("points", [])]
        spark = _sparkline(prices)
        change = h.get("change_7d", 0)
        arrow = "[green]↑[/]" if change > 0 else "[red]↓[/]" if change < 0 else "•"
        question = (m.get("question") or slug)[:55]
        lines.append(f"{question:<55} {m.get('yes_odds', 0):.2f} {spark} {arrow}{abs(change):+.3f}")
    return lines


def render_tick(state):
    grid = state.get("grid_snapshot", {}) or {}
    macro = state.get("macro_snapshot", {}) or {}
    feed = state.get("news_feed", {}) or {}
    poly = state.get("polymarket_snapshot", {}) or {}

    t = Table(show_header=False, expand=True, padding=(0, 1))
    t.add_row("[bold]tick[/]",
              f"#{state.get('tick_count', 0)}  {state.get('timestamp', '')}")
    t.add_row("[bold]grid[/]",
              f"fossil {grid.get('fossil_pct', 0)}% "
              f"(baseline {grid.get('baseline_fossil_pct', 0)}%) | "
              f"renewable {grid.get('renewable_pct', 0)}% | "
              f"src={grid.get('source', '?')}")
    t.add_row("[bold]macro[/]",
              f"MXN/USD {macro.get('mxn_usd', '?')} ({macro.get('mxn_usd_change_pct', 0)}%) | "
              f"WTI {macro.get('wti', '?')} ({macro.get('wti_change_pct', 0)}%)")
    t.add_row("[bold]news[/]",
              f"{feed.get('count', 0)} articles | tone {state.get('sentiment_score', 0):.2f}")
    spark_lines = _poly_sparklines(poly)
    poly_cell = f"{poly.get('count', 0)} relevant markets"
    if spark_lines:
        poly_cell += "\n" + "\n".join(spark_lines)
    t.add_row("[bold]polymarket[/]", poly_cell)

    score = state.get("contradiction_score", 0)
    color = "red" if score >= THRESHOLD else "yellow" if score >= THRESHOLD / 2 else "green"
    t.add_row("[bold]contradiction[/]", f"[{color}]{score:.2f}/10[/] (threshold {THRESHOLD})")

    reasoning = (state.get("analista_reasoning") or {}).get("reasoning", "")
    if reasoning:
        t.add_row("[bold]analista[/]", f"[italic]{reasoning}[/]")

    pred = state.get("testigo_prediction")
    if pred:
        t.add_row("[bold]testigo[/]",
                  f"{pred.get('market')} → {pred.get('direction')} "
                  f"(conf {pred.get('confidence', 0)})")

    console.print(Panel(
        t,
        title="[bold cyan]Caer y Reconstruir",
        subtitle=f"persistent memory: {len(load_predictions())} prediction entries",
    ))


def tick(state):
    state = observador.run(state)
    state = cronista.run(state)
    state = analista.run(state)
    state = testigo.run(state)
    render_tick(state)

    # Update verdicts on prior predictions against latest Polymarket snapshot
    n = update_verdicts(polymarket.slug_to_odds(state.get("polymarket_snapshot", {})))
    if n:
        console.print(f"[dim]updated {n} prior prediction verdicts[/]")

    log_tick({
        "tick": state.get("tick_count"),
        "contradiction_score": state.get("contradiction_score"),
        "fossil_pct": (state.get("grid_snapshot") or {}).get("fossil_pct"),
        "mean_tone": state.get("sentiment_score"),
        "cenace_source": (state.get("grid_snapshot") or {}).get("source"),
    })

    if state.get("contradiction_score", 0) >= THRESHOLD:
        console.rule(f"[bold red]CASE FILE — score {state['contradiction_score']:.2f}")
        state = event_matcher.run(state)
        echo = state.get("historical_echo")
        if echo and echo.get("event"):
            ev = echo["event"]
            console.print(
                f"[dim]historical echo: odds {echo['current_odds']} last seen "
                f"{echo['matched_date']} → {ev['date']} '{ev['headline'][:80]}' "
                f"(source={echo['source']})[/]"
            )
        elif echo:
            console.print(
                f"[dim]historical echo: odds {echo['current_odds']} last seen "
                f"{echo['matched_date']} (no curated event within ±3 days)[/]"
            )
        state = relator.run(state)
        if state.get("rag_citations"):
            cites = ", ".join(f"{c['source']} p.{c['page']}" for c in state["rag_citations"][:3])
            console.print(f"[dim]rag chunks: {cites}[/]")
        console.print(Panel(state.get("case_file") or "(empty)",
                            title="[bold red]case file",
                            border_style="red"))
        pred = state.get("testigo_prediction")
        if pred:
            log_prediction({
                "our_prediction": pred,
                "polymarket_odds_at_prediction": pred.get("current_odds"),
                "contradiction_score": state["contradiction_score"],
                "analista_reasoning": state.get("analista_reasoning"),
                "case_file": state["case_file"],
                "historical_echo": state.get("historical_echo"),
                "rag_citations": state.get("rag_citations"),
            })
            console.print(f"[green]logged prediction → {pred.get('market')} "
                          f"({pred.get('direction')})[/]")
        else:
            log_prediction({
                "our_prediction": None,
                "contradiction_score": state["contradiction_score"],
                "analista_reasoning": state.get("analista_reasoning"),
                "case_file": state["case_file"],
                "rag_citations": state.get("rag_citations"),
            })
    return state


def main():
    if not os.environ.get("NVIDIA_API_KEY"):
        console.print("[red bold]NVIDIA_API_KEY not set. Put it in .env. Exiting.[/]")
        return

    console.print("[bold cyan]Caer y Reconstruir — autonomous loop starting[/]")
    console.print(f"[dim]threshold={THRESHOLD}  interval={INTERVAL}s  "
                  f"model={os.getenv('NEMOTRON_MODEL', 'nvidia-nemotron-nano-9b-v2')}[/]")

    state = empty_state()
    while True:
        try:
            state = tick(state)
        except KeyboardInterrupt:
            console.print("\n[yellow]stopped by user[/]")
            break
        except Exception as e:
            console.print(f"[red]tick failed: {e}[/]")
            traceback.print_exc()
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
