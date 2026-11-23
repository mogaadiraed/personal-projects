"""CarbonSync assistant: read-only data tools, the Analyse engine and the dashboard guide.

The AI model (local or cloud) never computes figures itself. It calls the tools in
``tools.py``, which read DuckDB and the project files, and explains what they return.
``analyst.py`` holds the deterministic analysis (hotspots, LMDI decomposition, budget
gap, required reductions, lever ranking).
"""
