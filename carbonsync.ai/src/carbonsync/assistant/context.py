"""Read-only snapshot of everything the assistant can see, loaded once per data version.

The cache key is the DuckDB and config.yaml modification times plus the forecast run asked
for, so the snapshot refreshes by itself after an upload, a new forecast or a settings save.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from carbonsync.db import connect as db_connect
from carbonsync.export.insights import _pick_run, build_insights
from carbonsync.obs import get_logger

log = get_logger(__name__)

_LOCK = threading.Lock()
_CACHE: dict[tuple, DataContext] = {}


def get_project_root() -> Path:
    return Path(__file__).resolve().parents[3]


@dataclass
class DataContext:
    root: Path
    cfg: dict[str, Any]
    config_path: Path | None
    df: pd.DataFrame                 # emissions_daily, one row per day
    runs: pd.DataFrame               # forecast_runs, newest first
    run: pd.Series | None            # active forecast run (dashboard rule)
    fc: pd.DataFrame                 # its daily forecast: date, horizon, yhat, lo90, hi90
    _insights: dict[str, dict] = field(default_factory=dict, repr=False)

    @property
    def first(self) -> pd.Timestamp:
        return self.df["date"].min()

    @property
    def last(self) -> pd.Timestamp:
        return self.df["date"].max()

    @property
    def factors(self) -> dict[str, float]:
        return {k: float(v) for k, v in (self.cfg.get("emission_factors") or {}).items()}

    def insights(self, lang: str) -> dict[str, Any]:
        """The report insights (figures + conclusions), built once per language."""
        if lang not in self._insights:
            run_id = str(self.run["run_id"]) if self.run is not None else None
            self._insights[lang] = build_insights(lang, run_id=run_id, config_path=self.config_path)
        return self._insights[lang]

    def forecast_for(self, run_id: str | None) -> tuple[pd.DataFrame, pd.Series | None]:
        """Daily forecast of a given run (or the active one)."""
        if not run_id or (self.run is not None and str(self.run["run_id"]) == run_id):
            return self.fc, self.run
        match = self.runs[self.runs["run_id"] == run_id]
        if match.empty:
            raise ValueError(f"unknown forecast run_id: {run_id}")
        return _read_forecast(self._db_path(), run_id), match.iloc[0]

    def _db_path(self) -> Path:
        return self.root / self.cfg.get("paths", {}).get("duckdb", "data/carbonsync.duckdb")


def _read_forecast(db_path: Path, run_id: str) -> pd.DataFrame:
    conn = db_connect(str(db_path), read_only=True)
    try:
        fc = conn.execute("SELECT date, horizon, yhat, lo90, hi90 FROM forecasts WHERE run_id = ? ORDER BY horizon",
                          [run_id]).fetchdf()
    finally:
        conn.close()
    fc["date"] = pd.to_datetime(fc["date"])
    return fc


def load_context(run_id: str | None = None, config_path: Path | None = None) -> DataContext:
    """Cached snapshot; ``run_id`` selects a forecast run, otherwise the dashboard's active one."""
    root = get_project_root()
    cfg_file = config_path or root / "config.yaml"
    cfg = yaml.safe_load(Path(cfg_file).read_text(encoding="utf-8"))
    db_path = root / cfg.get("paths", {}).get("duckdb", "data/carbonsync.duckdb")
    key = (str(cfg_file), Path(cfg_file).stat().st_mtime_ns, db_path.stat().st_mtime_ns if db_path.exists() else 0, run_id)
    with _LOCK:
        if key in _CACHE:
            return _CACHE[key]
        conn = db_connect(str(db_path), read_only=True)
        try:
            df = conn.execute("SELECT * FROM emissions_daily ORDER BY date").fetchdf()
            runs = conn.execute("SELECT * FROM forecast_runs ORDER BY created_at DESC").fetchdf()
        finally:
            conn.close()
        df["date"] = pd.to_datetime(df["date"])
        run = _pick_run(runs, df["date"].max(), len(df), cfg, run_id) if not df.empty else None
        fc = _read_forecast(db_path, str(run["run_id"])) if run is not None else pd.DataFrame(
            columns=["date", "horizon", "yhat", "lo90", "hi90"])
        ctx = DataContext(root=root, cfg=cfg, config_path=config_path, df=df, runs=runs, run=run, fc=fc)
        stale = [k for k in _CACHE if k[:3] != key[:3]]  # older data versions are stale by definition
        for k in stale:
            del _CACHE[k]
        _CACHE[key] = ctx
        log.info("Assistant data snapshot: %d days to %s, forecast %s", len(df),
                 df["date"].max().date() if not df.empty else "—", run["run_id"] if run is not None else "none")
        return ctx
