#!/usr/bin/env bash
# CarbonSync AI - one-click launcher (Linux / macOS)
cd "$(dirname "$0")" || exit 1
PY=python3; [ -x .venv/bin/python ] && PY=.venv/bin/python
echo "Starting CarbonSync AI on http://localhost:8501"
exec "$PY" -m streamlit run streamlit_app.py
