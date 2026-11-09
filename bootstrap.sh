#!/usr/bin/env bash
# Caer y Reconstruir — cold-start bootstrap.
#
# Takes a freshly-extracted project directory and produces a runnable system:
#   1. checks Python 3.10+
#   2. creates a venv
#   3. installs requirements (torch + chromadb + everything)
#   4. scaffolds .env (interactive prompt for NVIDIA key)
#   5. detects this machine's first network IP and patches .streamlit/config.toml
#      so the dashboard's printed URL is reachable from another device
#   6. ingests rag_corpus/ if PDFs are present
#   7. prints exact run commands for the two-pane setup
#
# Usage:
#   cd caer_y_reconstruir
#   ./bootstrap.sh

set -e

PROJECT="Caer y Reconstruir"
VENV_DIR="venv"
PY_MIN="3.10"

color() { printf "\033[%sm%s\033[0m" "$1" "$2"; }
amber() { color "33"  "$1"; }
green() { color "32"  "$1"; }
red()   { color "31"  "$1"; }
dim()   { color "2"   "$1"; }
bold()  { color "1"   "$1"; }

echo
echo "================================================================"
amber "  $PROJECT — cold-start bootstrap"; echo
echo "================================================================"
echo

# -------- 1. Python --------
echo "[1/7] $(bold "Checking Python version...")"
if ! command -v python3 &>/dev/null; then
    red "  ERROR: python3 not found. Install Python ${PY_MIN}+ first."; echo
    exit 1
fi
PY_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$(echo "$PY_VER" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VER" | cut -d. -f2)
if (( PY_MAJOR < 3 )) || (( PY_MAJOR == 3 && PY_MINOR < 10 )); then
    red "  ERROR: Python ${PY_MIN}+ required, found $PY_VER"; echo
    exit 1
fi
green "  → Python $PY_VER ✓"; echo

# -------- 2. Project files --------
echo "[2/7] $(bold "Verifying project files...")"
for f in requirements.txt loop.py dashboard.py run.py; do
    if [[ ! -f "$f" ]]; then
        red "  ERROR: $f not found in $(pwd)"; echo
        red "  Are you running this from the project root?"; echo
        exit 1
    fi
done
green "  → project files present ✓"; echo

# -------- 3. Venv --------
echo "[3/7] $(bold "Setting up venv...")"
if [[ -d "$VENV_DIR" ]]; then
    dim "  → $VENV_DIR/ already exists, reusing"; echo
else
    python3 -m venv "$VENV_DIR"
    green "  → venv created at $VENV_DIR/"; echo
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

# -------- 4. Dependencies --------
echo "[4/7] $(bold "Installing dependencies...")"
dim "  (this can take 5+ minutes the first time — torch and chromadb are large)"; echo
pip install --upgrade pip -q
pip install -r requirements.txt
green "  → requirements installed ✓"; echo

# -------- 5. .env --------
echo "[5/7] $(bold "Configuring environment...")"
if [[ -f .env ]]; then
    dim "  → .env already exists, leaving its contents alone"; echo
else
    cat > .env <<'ENVEOF'
# REQUIRED: NVIDIA Cloud API key for Nemotron Nano 9B v2
# Get one free at https://build.nvidia.com (sign up → API keys)
# NOTE: the key MUST start with "nvapi-". Example:
#   NVIDIA_API_KEY=nvapi-xxxxxxxxxxxx
NVIDIA_API_KEY=

# OPTIONAL: Electricity Maps sandbox token for live Mexico grid data.
# Without it, the loop falls back to a CFE 2023 baseline (~65% fossil).
# Sign up free at https://www.electricitymaps.com/free-tier-api
# NOTE: variable name is ELECTRICITYMAP_TOKEN (one word, singular MAP).
ELECTRICITYMAP_TOKEN=

# OPTIONAL: enables live GDELT lookups in the dashboard's price-band panel
# when no curated timeline event matches a band. The dashboard's main
# news flow uses Google News RSS + GDELT and isn't gated by this flag.
ENABLE_GDELT_ECHO_FALLBACK=0

# OPTIONAL: local Gemma server URL for the non-reasoning Nemotron fallback.
# Falls back to the cloud Nemotron if this URL is unreachable. Safe to leave.
GEMMA_LOCAL_URL=http://localhost:8000/v1

# Loop tuning
CONTRADICTION_THRESHOLD=8.5
TICK_INTERVAL_SECONDS=60
NEMOTRON_MODEL=nvidia/nvidia-nemotron-nano-9b-v2
ENVEOF
    green "  → .env scaffolded"; echo
fi

# Prompt for NVIDIA_API_KEY ONLY when it's missing or empty in .env
# (whether .env was just created or already existed).
NEEDS_NVIDIA_KEY=1
if grep -qE '^NVIDIA_API_KEY=.+' .env 2>/dev/null; then
    NEEDS_NVIDIA_KEY=0
fi
NVIDIA_KEY=""
if [[ "$NEEDS_NVIDIA_KEY" == "1" ]] && [[ -t 0 ]]; then
    echo
    echo "  $(amber "NVIDIA_API_KEY is required and not set in .env.")"
    echo "  Enter it now (must start with $(bold "nvapi-")) or press Enter to skip."
    read -rp "  NVIDIA_API_KEY: " NVIDIA_KEY
    if [[ -n "$NVIDIA_KEY" ]]; then
        # Sanity check: warn if the user pasted a key without the nvapi- prefix
        if [[ ! "$NVIDIA_KEY" =~ ^nvapi- ]]; then
            amber "  ⚠ heads up: the key you pasted doesn't start with 'nvapi-'."; echo
            amber "    NVIDIA's API will reject it. I'm storing it as-is; edit"; echo
            amber "    .env if you need to fix the prefix."; echo
        fi
        # Cross-platform sed (Linux + macOS)
        if sed --version &>/dev/null; then
            sed -i "s|^NVIDIA_API_KEY=.*$|NVIDIA_API_KEY=$NVIDIA_KEY|" .env
        else
            sed -i '' "s|^NVIDIA_API_KEY=.*$|NVIDIA_API_KEY=$NVIDIA_KEY|" .env
        fi
        green "  → NVIDIA_API_KEY set in .env"; echo
    else
        amber "  → skipped; edit .env before running"; echo
    fi
elif [[ "$NEEDS_NVIDIA_KEY" == "0" ]]; then
    dim "  → NVIDIA_API_KEY already present in .env, leaving it"; echo
fi

# -------- 6. Network IP patching --------
echo "[6/7] $(bold "Detecting network IP for dashboard...")"
DETECTED_IP=""
if command -v hostname &>/dev/null; then
    DETECTED_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || true)
fi
if [[ -z "$DETECTED_IP" ]]; then
    DETECTED_IP=$(python3 -c 'import socket; s=socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(("8.8.8.8", 80)); print(s.getsockname()[0]); s.close()' 2>/dev/null || echo "")
fi
CONFIG=".streamlit/config.toml"
if [[ -n "$DETECTED_IP" ]] && [[ -f "$CONFIG" ]]; then
    CURRENT_IP=$(grep -E '^serverAddress' "$CONFIG" | sed -E 's/.*"([^"]+)".*/\1/' || echo "")
    if [[ "$CURRENT_IP" != "$DETECTED_IP" ]]; then
        echo "  current serverAddress: $(dim "$CURRENT_IP")"
        echo "  detected this machine:  $(amber "$DETECTED_IP")"
        if [[ -t 0 ]]; then
            read -rp "  Patch $CONFIG to use $DETECTED_IP? [y/N] " ANS
            if [[ "$ANS" =~ ^[Yy]$ ]]; then
                if sed --version &>/dev/null; then
                    sed -i "s|serverAddress = \".*\"|serverAddress = \"$DETECTED_IP\"|" "$CONFIG"
                else
                    sed -i '' "s|serverAddress = \".*\"|serverAddress = \"$DETECTED_IP\"|" "$CONFIG"
                fi
                green "  → patched"; echo
            else
                dim "  → kept existing serverAddress; edit $CONFIG manually if needed"; echo
            fi
        else
            dim "  → non-interactive shell; edit $CONFIG manually if needed"; echo
        fi
    else
        green "  → serverAddress already matches this machine ✓"; echo
    fi
else
    dim "  → could not detect IP or config.toml missing; skipping"; echo
fi

# -------- 7. RAG ingest --------
echo "[7/7] $(bold "Checking RAG corpus...")"
if [[ -d "rag_corpus" ]] && [[ -n "$(ls -A rag_corpus 2>/dev/null)" ]]; then
    if [[ -d "rag_index" ]] && [[ -n "$(ls -A rag_index 2>/dev/null)" ]]; then
        dim "  → rag_index/ already populated, skipping ingest"; echo
    else
        echo "  → ingesting PDFs from rag_corpus/ (one-time)..."
        python rag_ingest.py
        green "  → RAG index built"; echo
    fi
else
    dim "  → no rag_corpus/ found"; echo
    dim "    (RAG retrieval returns empty, loop and dashboard still work)"; echo
fi

# -------- Done --------
echo
echo "================================================================"
green "  Bootstrap complete!"; echo
echo "================================================================"
echo
echo "$(bold "Next steps:")"
echo
if [[ -z "$NVIDIA_KEY" ]] && ! grep -qE '^NVIDIA_API_KEY=.+' .env 2>/dev/null; then
    red "  ⚠ Edit .env and set NVIDIA_API_KEY first:"; echo
    echo "      \$ vi .env"
    echo
fi
echo "  $(bold "Two-pane run") (tmux recommended for headless SSH sessions):"
echo
echo "    $(amber "Pane 1") — autonomous loop:"
echo "        source venv/bin/activate"
echo "        python run.py"
echo
echo "    $(amber "Pane 2") — dashboard:"
echo "        source venv/bin/activate"
echo "        streamlit run dashboard.py"
echo
if [[ -n "$DETECTED_IP" ]]; then
    echo "  Open the dashboard at:  $(green "http://$DETECTED_IP:8501")"
else
    echo "  Open the dashboard at the URL Streamlit prints in pane 2."
fi
echo
echo "  Full guide: $(amber "README_EN.md")"
echo
