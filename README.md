# Caer y Reconstruir: A Multi-Agent Monitor of Mexico's Energy Transition

Multi-agent intelligence system monitoring Mexico's energy transition in real time.

The agent watches CENACE (grid), MXN/USD, WTI, GDELT (news), and Polymarket. When
ground-level data diverges from the official narrative or market consensus, El Relator
generates a case file documenting the divergence.

## One-time setup (on the DGX Spark)

```bash
cd ~/caer_y_reconstruir

# Python env
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Config
cp .env.example .env
# Edit .env: paste your NVIDIA_API_KEY from build.nvidia.com
```

## Smoke tests (run each, confirm output looks reasonable)

```bash
source .venv/bin/activate

python nemotron_client.py        # should print something like "alive"
python -m fetchers.cenace        # JSON dict, source=live OR fallback
python -m fetchers.macro         # JSON dict with mxn_usd and wti
python -m fetchers.gdelt         # JSON dict with article count
python -m fetchers.polymarket    # JSON dict with markets count
```

## Run the agent

```bash
source .venv/bin/activate
python run.py
```

Stop with Ctrl-C. The loop ticks every 60s by default. When `contradiction_score`
exceeds the threshold (default 6.0), El Relator generates a case file and the
prediction is logged to `memory/predictions.jsonl`.

## Architecture

| Agent | Role | LLM |
|---|---|---|
| El Observador | pulls live data from 4 sources | none |
| El Cronista | aggregates GDELT-precomputed tone | none (deterministic) |
| El Analista | computes contradiction score | Nemotron |
| El Testigo | picks which Polymarket market diverges most | Nemotron |
| El Relator | writes the 5-paragraph case file | Nemotron |
| La Memoria | persistent JSONL log of predictions + verdicts | filesystem |

## Persistent memory

- `memory/predictions.jsonl` — every prediction + later verdict (`market caught up`,
  `gap persists`, `we were wrong`)
- `memory/ticks.jsonl` — every tick's signals (for offline analysis)

Survives restarts. Demo line: "the agent has been running for N hours and has logged
M predictions across restarts."

## Demo

The `contradiction_score` is shown in green/yellow/red on every tick. The case file
prints in a red panel when the threshold trips. Tail `memory/predictions.jsonl` in
another terminal during the demo to show persistence.

## Tuning the threshold

Default is 6.0. Watch the first 5–10 ticks. If the score never crosses 6, lower the
threshold in `.env`. If it crosses on every tick, raise it. You want one case file
every ~5–15 ticks for a good demo cadence.
