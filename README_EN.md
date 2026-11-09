# Caer y Reconstruir — Market Investigator

*"To fall and rebuild"* — a 7-agent autonomous monitor for Mexico's energy
transition, built for the NVIDIA Hack-a-Claw hackathon. Runs on an NVIDIA
DGX Spark with Nemotron Nano 9B v2 in the cloud.

This document is the English-language guide to the project: what it is, how
each piece works, how to run it, and how to drive the dashboard during the demo.

---

## 1. What the project does

The system detects contradictions between Mexico's official energy-transition
narrative (renewables share, decarbonization goals, currency stability) and
ground-level data (live grid mix, oil prices, FX, news sentiment, prediction
markets). When it sees a divergence, it writes a "case file" naming the
contradiction and predicts which prediction market the divergence implies
mispricing in.

The dashboard is the "reveal" layer: judges click into any trending Polymarket
market and see live prices, lifetime price charts with structural pivot points
(higher highs / lower highs / higher lows / lower lows), each pivot interpreted
by Nemotron against real news headlines, and a Narrator-written briefing
explaining what the market means and what the current price implies.

---

## 2. The 7 agents

The agents live in `agents/` and are coordinated by the loop in `loop.py`.
Each tick (every 60s), the loop runs the agents in sequence and logs the
result to `memory/ticks.jsonl`. When the contradiction score crosses a
threshold, the Reporter also writes a case file to `memory/predictions.jsonl`.

| Spanish name | English name | What it does |
|---|---|---|
| **Observador** | Observer | Pulls fresh data from all sources: CENACE grid mix, yfinance macro (MXN/USD, WTI), GDELT news, Polymarket markets. No LLM call. |
| **Cronista** | Chronicler | Runs VADER sentiment analysis on English-language news titles to compute a mean tone score for the tick. No LLM call. |
| **Analista** | Analyst | Asks Nemotron to score a `contradiction_score` 0–10 based on the snapshot. Higher = bigger gap between official narrative and observed data. |
| **Testigo** | Witness | Picks the Polymarket market most affected by the contradiction and predicts a direction (`increase` / `decrease` in YES odds). |
| **Relator** | Reporter | Only fires when `contradiction_score ≥ threshold` (default 8.5). Writes a longer narrative "case file" explaining the contradiction. Grounded with RAG from a 7-PDF green-finance corpus. |
| **Inspector** | Inspector | (Dashboard agent, not part of the loop.) For a selected market, finds structural pivot points on the lifetime price curve and calls Nemotron against news headlines to explain each one. |
| **Narrador** | Narrator | (Dashboard agent.) Translates raw data into demo-ready prose: market briefings, price journey narratives, and loop activity summaries. |

---

## 3. Data sources

| Source | Used by | What it provides |
|---|---|---|
| **Polymarket Gamma API** | Observer + dashboard | Trending markets with question, slug, YES odds, 24h volume, 7-day price history |
| **Polymarket CLOB REST** | dashboard | Full-lifetime daily YES-odds curve for any market |
| **Polymarket CLOB Websocket** | dashboard | Live book + last-trade feed for the selected market (`wss://ws-subscriptions-clob.polymarket.com/ws/market`) |
| **Electricity Maps** (sandbox tier) | Observer | Live Mexico grid mix (fossil %, renewable %) with a CFE 2023 baseline fallback |
| **yfinance** | Observer | MXN/USD, WTI crude — macro context |
| **GDELT v2 DOC API** | Observer + Inspector fallback | Global news article list. Throttled at 30s/call. Used for tone analysis in the loop. |
| **Google News RSS** | Inspector primary | Date-anchored news search via `after:YYYY-MM-DD before:YYYY-MM-DD` operators. No auth, no rate limit, mainstream-source bias. |
| **NVIDIA Nemotron Nano 9B v2** | Analyst, Witness, Reporter, Inspector, Narrator | All reasoning calls. OpenAI-compat client against `integrate.api.nvidia.com/v1`. |
| **Green-finance PDF corpus** | Reporter, Analyst | 7 PDFs / 331 chunks indexed in ChromaDB with sentence-transformers `all-MiniLM-L6-v2`. RAG-injected into Reporter and Analyst prompts. |

---

## 4. File layout

```
caer_y_reconstruir/
├── run.py                       # Entry point: starts loop.main()
├── loop.py                      # 60s tick loop, coordinates the 5 loop agents
├── state.py                     # Shared state schema (TypedDict)
├── memory.py                    # JSONL persistence (ticks, predictions, verdicts)
├── event_matcher.py             # Historical-price-echo lookup vs curated timeline
├── events_timeline.json         # Curated 41-event Mexico-energy timeline
├── rag.py                       # ChromaDB retrieval helpers
├── rag_ingest.py                # PDF → chunks → ChromaDB pipeline
├── nemotron_client.py           # OpenAI-compat client for Nemotron + local Gemma
│
├── dashboard.py                 # Streamlit dashboard (main UI)
├── dashboard_theme.py           # Bloomberg-terminal CSS + render helpers
│
├── agents/
│   ├── observador.py            # Observer
│   ├── cronista.py              # Chronicler
│   ├── analista.py              # Analyst
│   ├── testigo.py               # Witness
│   ├── relator.py               # Reporter
│   ├── inspector.py             # Inspector (dashboard)
│   └── narrator.py              # Narrator (dashboard)
│
├── fetchers/
│   ├── cenace.py                # Electricity Maps + CFE baseline
│   ├── macro.py                 # yfinance MXN/USD + WTI
│   ├── gdelt.py                 # GDELT article + tone fetcher
│   ├── google_news.py           # Google News RSS (no auth)
│   ├── polymarket.py            # Gamma REST + CLOB REST history
│   └── polymarket_ws.py         # CLOB websocket client (threaded singleton)
│
├── memory/
│   ├── ticks.jsonl              # Append-only loop tick log
│   └── predictions.jsonl        # Append-only prediction + verdict log
│
├── .streamlit/
│   └── config.toml              # Bloomberg theme + Tailscale URL pin
│
├── .env                         # NVIDIA_API_KEY, ELECTRICITYMAP_TOKEN, etc.
├── requirements.txt
└── README_EN.md                 # This file
```

---

## 5. How to run

### 5.0 Cold-start on a fresh machine (recommended)

If you've just received the project as a tarball / zip and have nothing
installed yet, the included `bootstrap.sh` automates everything below.

**Prerequisites** (these must exist on the machine first):
- Linux or macOS (Windows: use WSL2)
- Python **3.10 or newer** (`python3 --version`)
- ~10 GB free disk space (torch + chromadb + sentence-transformers wheels)
- A network connection (for `pip install` and Polymarket/Google News/NVIDIA APIs)

**API keys** (only NVIDIA is strictly required):
| Key | Required | Where to get it (free) |
|---|---|---|
| `NVIDIA_API_KEY` | **yes** | <https://build.nvidia.com> — sign up, then "API keys" tab |
| `ELECTRICITYMAP_TOKEN` | optional | <https://www.electricitymaps.com/free-tier-api> — sandbox tier is fine; without it the loop uses a CFE 2023 baseline |

**Then, one command:**
```bash
# extract the project (paths inside the tarball are relative to caer_y_reconstruir/)
mkdir caer_y_reconstruir && cd caer_y_reconstruir
tar xzf ../caer_update.tar.gz   # or however you received it

# run bootstrap
./bootstrap.sh
```

`bootstrap.sh` walks through:
1. Verifies Python ≥ 3.10
2. Checks project files are present
3. Creates `venv/` and activates it
4. `pip install -r requirements.txt` (slow first time — torch is large)
5. Scaffolds `.env` from a template and **prompts interactively for your NVIDIA API key**
6. Detects this machine's first network IP and **offers to patch `.streamlit/config.toml`** so the dashboard's URL is reachable from your laptop instead of the original Tailscale IP (100.119.28.20)
7. Ingests `rag_corpus/*.pdf` into ChromaDB if any PDFs are present (skips otherwise — RAG-less mode still runs the loop)

When it finishes, it prints the exact two-pane run commands and the dashboard URL for *this* machine.

**About `rag_corpus/`:** the green-finance PDF corpus is ~50 MB of curated reports. If your tarball doesn't include `rag_corpus/`, you can either:
- Skip RAG — the loop runs fine without it; the Reporter / Analyst prompts just won't have grounded citations.
- Drop your own PDFs into `rag_corpus/` before running `bootstrap.sh` and they'll be ingested.

**About `events_timeline.json`:** the 41-event Mexico-energy timeline is hand-curated and ships in the project root. Used by `event_matcher.py` for the historical-echo feature. Already present in the bundle — nothing to do.

---

### 5.1 First-time setup (Spark)

```bash
cd ~/Documents/server_projects/users/jeremy/caer_y_reconstruir

# venv
python3 -m venv venv
source venv/bin/activate

# install
pip install -r requirements.txt

# (one-time) build the RAG index from PDFs in rag_corpus/
python rag_ingest.py

# environment
cat > .env <<'EOF'
NVIDIA_API_KEY=<your nvidia cloud key>
ELECTRICITYMAP_TOKEN=<your sandbox token>   # optional, baseline fallback if absent
CONTRADICTION_THRESHOLD=8.5
TICK_INTERVAL_SECONDS=60
EOF
```

### 5.2 Running the system (two terminal panes)

**Pane 1 — the autonomous loop (drives data into memory/):**
```bash
cd ~/Documents/server_projects/users/jeremy/caer_y_reconstruir
source venv/bin/activate
python run.py
```
You should see a rich-formatted tick panel every 60 seconds with grid /
macro / news / Polymarket / contradiction-score lines.

**Pane 2 — the dashboard (reveal layer):**
```bash
cd ~/Documents/server_projects/users/jeremy/caer_y_reconstruir
source venv/bin/activate
streamlit run dashboard.py
```
The terminal will print a clickable URL. Open it on your laptop:

> **`http://100.119.28.20:8501`**

(The Tailscale IP and port are pinned in `.streamlit/config.toml`.)

### 5.3 Updating from a tarball

When you receive a `caer_update.tar.gz` from the dev box:

```bash
cd ~/Documents/server_projects/users/jeremy/caer_y_reconstruir
tar xzf caer_update.tar.gz && rm caer_update.tar.gz

# if requirements.txt changed:
pip install -r requirements.txt

# restart whatever changed:
#   if dashboard files changed → ^C and restart streamlit
#   if loop files changed → ^C and restart run.py
```

---

## 6. Dashboard panel guide

The dashboard is a single page divided into these sections, top to bottom:

### Hero strip (always visible)
- **Market Investigator tile** with a pulsing dot — loop heartbeat. Green = last tick < 3 min ago (alive). Red = stalled. Hover for the full explanation of what the loop is.
- **Ticks logged** — count of `memory/ticks.jsonl` entries.
- **Last contradiction** — most recent score 0–10. Color-coded (red ≥ 6, amber ≥ 3, green < 3).
- **Last case file** — UTC timestamp of the most recent prediction entry.
- **Grid** — `LIVE` (Electricity Maps real data) or `BASELINE` (CFE 2023 fallback) + current fossil %.

### Sidebar — trending markets
- **Toggles:**
  - `info-rich only` (default on): hides markets with volume < $1M, odds outside 0.05–0.95, or 7-day range < 0.02.
  - `hide known-empty markets` (default on): once you've investigated a market and found it has no usable catalysts (✗), it disappears from the sidebar.
- **Each market card** shows: ✓/◐/✗/· health icon · question (truncated) · YES odds · sparkline · volume · 7-day change · matched keywords (which words from the relevance filter earned it a slot).
- **WS status** at the bottom: connection state + active subscription count + reconnect history.

### Selected market view (after clicking a sidebar entry)

1. **Market title + slug + token_id** (debug info dimmed).
2. **Live ticker** — auto-refreshes every 3 seconds from the websocket:
   - LIVE PRICE (canonical when WS has data; REST shown as delta below).
   - BEST BID (green) · BEST ASK (red) · SPREAD.
   - RECENT TRADES — last 6 trades with timestamp, side (buy/sell), price, size.
   - Mini-chart of in-session mid-price ticks (only appears once enough data has streamed).
3. **Narrator · Market briefing** (amber-bordered) — two paragraphs:
   - What the market is asking and what the current YES odds imply in plain probability language.
   - How the lifetime price journey repriced over time.
4. **Lifetime price + rolling band chart** with:
   - Amber price line.
   - Cyan dashed rolling 14-day mean + translucent ±2σ band.
   - All detected pivot points as colored dots: 🟢 higher high · 🟠 lower high · 🔵 higher low · 🔴 lower low. Large dots = interpreted (have a catalyst card below). Small outlined dots = detected but not interpreted.
   - Y-axis padded by 10% so peaks have headroom.
5. **Right-side metric tiles**: LIFETIME PTS · RANGE · REST 24h VOL · REST YES · 7d trajectory mini-chart.
6. **Inspector — pivot catalysts**:
   - Top pivots by prominence, each as an HH/LH/HL/LL-colored card.
   - Each card shows: date · magnitude · z-score · before→after price · rolling mean · trend interpretation · Nemotron's one-sentence catalyst · catalyst source domain · `via google news` or `via gdelt` provenance · model confidence.
   - **Pivots without catalysts are never shown** (always-on filter).
   - Each card has an expander with the underlying headlines.
7. **Narrator · Price journey** (amber-bordered) — one paragraph narrating how the market traveled across conviction regimes (long-shot → uncertain → high-conviction → back).
8. **Agent context** (collapsed expanders) — prior predictions the loop's Witness made about this market, with verdicts (`market caught up` / `gap persists` / `we were wrong`).

### Loop activity (bottom of page, always visible)
- **Narrator · Current state** (cyan-bordered) — one paragraph interpreting the last 12 ticks for the audience: what the contradiction trend, fossil %, and news tone are telling us about Mexico's energy economy right now.
- **Raw tick log** (collapsed expander) — `logged_at · tick · contradiction · fossil_% · mean_tone · grid_src` for the last 12 ticks.

---

## 7. Demo workflow

### Before going on stage

1. **Start the loop early** — at least 30 minutes before. Each tick is 60 seconds, so 30 ticks = solid background data for the Narrator's loop briefing to be substantive.
2. **Open the dashboard** and start clicking through markets to warm the caches. Each fresh market triggers Nemotron calls (briefing + journey + 3 pivot interpretations) — roughly 10–15 seconds per market on first load, then cached 15 min.
3. **Curate the sidebar** — markets that come back with no usable catalysts auto-hide. Click through your candidates and let the system filter to demo-ready (✓) markets. Aim for 3–5 good ones.
4. **Note your "money shots"** — markets where the Inspector found clear catalysts that connect to your Mexico narrative. Examples that have worked well:
   - "Will the U.S. invade Iran before 2027?" — oil price implications for Mexico's fossil-export revenue.
   - "Will the Fed decrease interest rates by 50+ bps after June 2026 meeting?" — peso pressure / capital flows.
   - "Will Pemex announce…" type markets — direct.

### On stage

1. Open with the hero strip pulsing — *"this loop is reading Mexico's energy state right now, 60 seconds at a time"*.
2. Read the **Narrator · Current state** panel out loud — it gives you the elevator pitch on what the system has detected today.
3. Click your first money-shot market. The **Narrator · Market briefing** tells the audience what they're looking at. The chart with HH/LH/HL/LL dots is the visual hook.
4. Walk through 1–2 Inspector cards — the Nemotron-written catalysts from real news headlines.
5. Read the **Narrator · Price journey** paragraph — that's your closing narrative for the market.
6. Repeat with a second market to show breadth.
7. Optional close: show the agent-context expanders for a market the system has been tracking through multiple ticks — proof of persistent memory and self-correction.

---

## 8. Tweakable thresholds (env vars)

| Variable | Default | Effect |
|---|---|---|
| `CONTRADICTION_THRESHOLD` | `8.5` | Score above which Reporter fires and writes a case file. Lower = more case files. |
| `TICK_INTERVAL_SECONDS` | `60` | Loop cadence. Lower = more API quota burn. |
| `NEMOTRON_MODEL` | `nvidia/nvidia-nemotron-nano-9b-v2` | Override to test other models. |
| `ENABLE_GDELT_ECHO_FALLBACK` | unset | If `1`/`true`, the dashboard's price-band panel falls back to live GDELT lookups when no curated timeline event matches a band. |

---

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Dashboard hero strip shows red dot ("loop stalled") | `run.py` died or hasn't started | Restart `python run.py` in pane 1 |
| Live ticker stuck on `awaiting first WS event` | Polymarket websocket hasn't sent a book/trade yet, or connection dropped | Check sidebar WS status line. If `reconnecting…`, wait. If subs > 0 and connected, just wait — book events arrive on first quote change. |
| `Inspector` shows "All pivots came back without catalysts" | Genuinely no news found in any ±7-day window for any of the market's pivots — even with Google News + GDELT fallback | Don't demo this market. The sidebar will auto-hide it. |
| First-click on a market takes 15+ seconds | Cold cache — Nemotron + Google News calls run for briefing + journey + 3 pivot interpretations | Expected. Warm markets before stage. |
| Hero "grid: BASELINE" instead of "LIVE" | Electricity Maps sandbox token quota exhausted | Use baseline. Loop continues with CFE 2023 fossil %. |
| `pip install` fails on Spark | venv not activated | `source venv/bin/activate` |
| `bootstrap.sh` exits at Python check | Wrong Python version (need ≥ 3.10) | Install Python 3.10+ via your distro's package manager, then re-run |
| `bootstrap.sh` says "no rag_corpus/ found" | PDF corpus wasn't shipped in your bundle | OK to ignore — loop and dashboard run without RAG. Drop PDFs into `rag_corpus/` later and re-run `python rag_ingest.py` whenever you want it. |
| Dashboard URL prints but is unreachable from your laptop | `.streamlit/config.toml`'s `serverAddress` doesn't match this machine's IP | Re-run `bootstrap.sh` (it offers to patch the IP), or edit the file manually and restart streamlit |
| Streamlit URL not reachable from laptop | Tailscale tunnel not up, or wrong IP in `.streamlit/config.toml` | Confirm `tailscale status` on Spark and update the `serverAddress` line. |

---

## 10. What was built for this hackathon

Phase 1 (baseline 5-agent loop):
- Loop coordinator with 60s tick + rich terminal output
- Observador, Cronista, Analista, Testigo, Relator agents
- JSONL persistence + verdict re-scoring

Phase 2 (depth):
- RAG over 7-PDF green-finance corpus (331 chunks)
- Historical-echo matching against 41-event curated timeline
- Streamlit dashboard with lifetime charts + curated price-band history

Phase 3 (this round, for the demo):
- Bloomberg-terminal theme (black + amber + monospace)
- Live Polymarket websocket client (threaded singleton, auto-reconnect)
- Inspector agent with rolling-mean band + pivot point detection (HH/LH/HL/LL)
- Smart GDELT query builder (drops stop words, prefers proper nouns)
- Google News RSS as primary news source (GDELT fallback)
- Adaptive price bands (replaced with AI-narrated price journey)
- Market-health tracking + sidebar auto-curation
- Narrator agent: market briefing + price journey + loop summary
- Always-on hiding of empty pivot cards

---

**Built on a DGX Spark · NVIDIA Hack-a-Claw · 2026-05-16**
