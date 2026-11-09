#!/usr/bin/env bash
# Stable dashboard launcher.
# Config (port, address, canonical URL) lives in .streamlit/config.toml.
# This script just activates venv, kills any prior streamlit, and runs.
set -uo pipefail

cd "$(dirname "$0")"
if [ -z "${VIRTUAL_ENV:-}" ] && [ -f venv/bin/activate ]; then
    # shellcheck disable=SC1091
    source venv/bin/activate
fi

pkill -f "streamlit run dashboard.py" 2>/dev/null || true
sleep 1

exec streamlit run dashboard.py
