"""
CarbonSync AI – Cached Data Access Layer with Auto-Invalidation
================================================================
Provides high-performance, cached queries against DuckDB tables and JSON
manifests, featuring automatic cache invalidation and connection pooling.
"""
from __future__ import annotations

import json
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import streamlit as st
import yaml

from carbonsync.obs import get_logger

log = get_logger("carbonsync.app.data")


def get_project_root() -> Path:
    """Returns absolute path to the project root directory."""
    return Path(__file__).resolve().parents[2]


@st.cache_data(show_spinner=False)
def load_config() -> dict[str, Any]:
    """Loads and caches config.yaml from project root."""
    config_path = get_project_root() / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_config(new_config: dict[str, Any]) -> Path:
    """
    Saves updated configuration to config.yaml and creates a timestamped
    audit copy in data/config_history/.
    """
    root = get_project_root()
    cfg_path = root / "config.yaml"
    history_dir = root / "data" / "config_history"
    history_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = history_dir / f"config_{timestamp}.yaml"

    # Save backup of current config
    if cfg_path.exists():
        shutil.copy2(cfg_path, backup_path)

    # Save new config
    with open(cfg_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(new_config, f, sort_keys=False, allow_unicode=True)

    # Clear config cache
    load_config.clear()
    return backup_path


def get_duckdb_path() -> Path:
    """Resolves path to DuckDB database file."""
    cfg = load_config()
    rel_path = cfg.get("paths", {}).get("duckdb", "data/carbonsync.duckdb")
    return get_project_root() / rel_path


@st.cache_resource(show_spinner=False)
def get_duckdb_connection():
    """
    Returns a persistent read-only connection to the DuckDB database.
    Re-used across all query calls for minimal connection overhead.
    """
    db_path = get_duckdb_path()
    if not db_path.exists():
        return None
    return duckdb.connect(str(db_path), read_only=True)


def _get_db_mtime() -> float:
    """Returns file modification time of DuckDB file for cache keying."""
    p = get_duckdb_path()
    return p.stat().st_mtime if p.exists() else 0.0


@st.cache_data(show_spinner=False, ttl=300)
def load_emissions_daily(_mtime: float | None = None) -> pd.DataFrame:
    """Loads historical daily emissions and operational metrics from DuckDB."""
    db_path = get_duckdb_path()
    if not db_path.exists():
        return pd.DataFrame()

    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        t0 = time.perf_counter()
        df = conn.execute("SELECT * FROM emissions_daily ORDER BY date ASC").fetchdf()
        log.info("DuckDB read emissions_daily: %d rows (%.0f ms)", len(df), (time.perf_counter() - t0) * 1000)
        df["date"] = pd.to_datetime(df["date"])
        return df
    finally:
        conn.close()


@st.cache_data(show_spinner=False, ttl=300)
def load_daily_raw_features(_mtime: float | None = None) -> pd.DataFrame:
    """Loads daily merged features table (with imputation flags) from DuckDB."""
    db_path = get_duckdb_path()
    if not db_path.exists():
        return pd.DataFrame()

    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        df = conn.execute("SELECT * FROM daily ORDER BY date ASC").fetchdf()
        log.info("DuckDB read daily: %d rows", len(df))
        df["date"] = pd.to_datetime(df["date"])
        return df
    finally:
        conn.close()


@st.cache_data(show_spinner=False, ttl=300)
def load_forecast_runs(_mtime: float | None = None) -> pd.DataFrame:
    """Loads metadata for all registered forecast runs from DuckDB."""
    db_path = get_duckdb_path()
    if not db_path.exists():
        return pd.DataFrame()

    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        df = conn.execute("SELECT * FROM forecast_runs ORDER BY created_at DESC").fetchdf()
        log.info("DuckDB read forecast_runs: %d runs", len(df))
        if "created_at" in df.columns:
            df["created_at"] = pd.to_datetime(df["created_at"])
        return df
    finally:
        conn.close()


@st.cache_data(show_spinner=False, ttl=300)
def load_forecast_by_run(run_id: str, _mtime: float | None = None) -> pd.DataFrame:
    """Loads forecast predictions for a specific run ID."""
    db_path = get_duckdb_path()
    if not db_path.exists():
        return pd.DataFrame()

    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        df = conn.execute(
            "SELECT * FROM forecasts WHERE run_id = ? ORDER BY horizon ASC",
            [run_id],
        ).fetchdf()
        log.info("DuckDB read forecasts for run %s: %d days", run_id, len(df))
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
        return df
    finally:
        conn.close()


@st.cache_data(show_spinner=False, ttl=300)
def load_latest_forecast(_mtime: float | None = None) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Loads the latest stored forecast series and its run metadata."""
    runs = load_forecast_runs()
    if runs.empty:
        return pd.DataFrame(), {}

    latest_run = runs.iloc[0].to_dict()
    run_id = str(latest_run["run_id"])
    df_fc = load_forecast_by_run(run_id)
    return df_fc, latest_run


@st.cache_data(show_spinner=False, ttl=300)
def load_backtest_summary() -> list[dict[str, Any]]:
    """
    Loads backtest benchmark evaluation results across models.
    Supports both dictionary with 'results' list and legacy flat lists.
    """
    summary_path = get_project_root() / "data" / "backtest_summary.json"
    if summary_path.exists():
        with open(summary_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data.get("results", [])
            return data

    # Fallback to DuckDB backtest_results table
    db_path = get_duckdb_path()
    if db_path.exists():
        conn = duckdb.connect(str(db_path), read_only=True)
        try:
            df = conn.execute(
                """
                SELECT model_name, history_months,
                       AVG(mae) as mae, AVG(mase) as mase,
                       AVG(coverage) as coverage, AVG(mean_width) as mean_width
                FROM backtest_results
                GROUP BY model_name, history_months
                ORDER BY model_name, history_months
                """
            ).fetchdf()
            return df.to_dict(orient="records")
        finally:
            conn.close()

    return []


@st.cache_data(show_spinner=False)
def load_backtest_meta() -> dict[str, Any]:
    """Loads metadata documentation from backtest_summary.json (R1 requirement)."""
    summary_path = get_project_root() / "data" / "backtest_summary.json"
    if summary_path.exists():
        with open(summary_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data.get("meta", {})
    return {}


@st.cache_data(show_spinner=False)
def load_data_quality_report() -> dict[str, Any]:
    """Loads data quality ingestion metrics report."""
    report_path = get_project_root() / "data" / "data_quality_report.json"
    if report_path.exists():
        with open(report_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


@st.cache_data(show_spinner=False)
def load_quarantine_details() -> list[dict[str, Any]]:
    """Loads all quarantine error reports from data/quarantine/*.errors.json."""
    quarantine_dir = get_project_root() / "data" / "quarantine"
    records: list[dict[str, Any]] = []

    if not quarantine_dir.exists():
        return records

    for err_file in sorted(quarantine_dir.glob("*.errors.json")):
        try:
            with open(err_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                records.append(data)
        except (json.JSONDecodeError, OSError):
            continue

    return records


@st.cache_data(show_spinner=False, ttl=300)
def load_monthly_scope_summary() -> pd.DataFrame:
    """Precomputes and returns monthly emissions aggregated by scope."""
    df_daily = load_emissions_daily()
    if df_daily.empty:
        return pd.DataFrame()

    df = df_daily.copy()
    df["month_dt"] = df["date"].dt.to_period("M").dt.to_timestamp()
    agg = (
        df.groupby("month_dt")[["co2e_scope1_t", "co2e_scope2_t", "co2e_other_t", "co2e_total_t"]]
        .sum()
        .reset_index()
    )
    return agg



def load_latest_runs_by_model() -> dict[str, dict[str, Any]]:
    """Most recent stored run metadata for each model name."""
    runs = load_forecast_runs()
    out: dict[str, dict[str, Any]] = {}
    for _, row in runs.iterrows():  # already sorted newest first
        out.setdefault(str(row["model_name"]), row.to_dict())
    return out


def load_active_forecast() -> tuple[pd.DataFrame, dict[str, Any]]:
    """Forecast used across the dashboard.

    Order of preference: the run chosen on the Forecast page (session), then the
    latest run of the model the router would pick for the full history, then the
    latest run overall.
    """
    runs = load_forecast_runs()
    if runs.empty:
        return pd.DataFrame(), {}

    chosen = st.session_state.get("active_run_id")
    if chosen and chosen in set(runs["run_id"]):
        meta = runs[runs["run_id"] == chosen].iloc[0].to_dict()
        return load_forecast_by_run(chosen), meta

    from carbonsync.models.router import choose_model

    cfg = load_config()
    df = load_emissions_daily()
    preferred = choose_model(len(df), cfg)

    # Only runs trained up to the latest observed day are current
    last = df["date"].max() if not df.empty else None
    aligned = runs[pd.to_datetime(runs["end_date"]) == last] if last is not None else runs.iloc[0:0]
    pool = aligned if not aligned.empty else runs
    pref_runs = pool[pool["model_name"] == preferred]
    meta = (pref_runs.iloc[0] if not pref_runs.empty else pool.iloc[0]).to_dict()
    meta["stale"] = aligned.empty
    return load_forecast_by_run(str(meta["run_id"])), meta


@st.cache_data(show_spinner=False, ttl=300)
def load_department_status() -> pd.DataFrame:
    """Per department: last reported day and days present per month (from the raw DuckDB tables)."""
    db_path = get_duckdb_path()
    tables = {"energie": "raw_energy", "production": "raw_production",
              "logistique": "raw_logistics", "dechets_eau": "raw_waste_water"}
    if not db_path.exists():
        return pd.DataFrame()
    conn = duckdb.connect(str(db_path), read_only=True)
    frames = []
    try:
        for dept, table in tables.items():
            try:
                d = conn.execute(
                    f"SELECT date_trunc('month', date) AS month, COUNT(*) AS days FROM {table} GROUP BY 1"
                ).fetchdf()
            except duckdb.Error:
                continue
            d["department"] = dept
            frames.append(d)
    finally:
        conn.close()
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    out["month"] = pd.to_datetime(out["month"])
    log.info("DuckDB read department coverage: %d department-months", len(out))
    return out


@st.cache_data(show_spinner=False, ttl=300)
def load_department_last_dates() -> dict[str, pd.Timestamp | None]:
    """Last reported day per department (raw tables, before the common date spine)."""
    tables = {"energie": "raw_energy", "production": "raw_production",
              "logistique": "raw_logistics", "dechets_eau": "raw_waste_water"}
    out: dict[str, pd.Timestamp | None] = dict.fromkeys(tables)
    db_path = get_duckdb_path()
    if not db_path.exists():
        return out
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        for dept, table in tables.items():
            try:
                v = conn.execute(f"SELECT MAX(date) FROM {table}").fetchone()[0]
            except duckdb.Error:
                v = None
            out[dept] = pd.Timestamp(v) if v is not None else None
    finally:
        conn.close()
    return out


def _alerts_mtime() -> float:
    p = get_project_root() / "data" / "alerts.json"
    return p.stat().st_mtime if p.exists() else 0.0


@st.cache_data(show_spinner=False)
def _alerts_state(mtime: float) -> dict[str, Any]:  # mtime: part of the cache key
    from carbonsync.alerts import read_state

    return read_state(get_project_root())


def load_alerts_state() -> dict[str, Any]:
    """Saved alerts (``data/alerts.json``, written by the pipeline and the Alerts page); re-read when it changes."""
    return _alerts_state(_alerts_mtime())


@st.cache_data(show_spinner=False, ttl=300)
def load_year_projection(settings_key: str) -> dict[str, Any]:  # settings_key: targets + budget, part of the cache key
    """Full-year projection of the year being managed (actual + forecast + seasonal estimate), as the assistant."""
    from carbonsync.assistant import analyst
    from carbonsync.assistant.context import load_context

    ctx = load_context()
    p = analyst.project_year(ctx, analyst.hotspots(ctx, analyst.TREND_MONTHS))
    return {"year": int(p["year"]), "total": p["total_t"], "lo": p["total_lo_t"], "hi": p["total_hi_t"],
            "cap": p["cap"], "gap": p["gap_t"]}


def clear_caches() -> None:
    """Drop every cached query so all pages re-read the database."""
    st.cache_data.clear()
    log.info("Dashboard caches cleared – pages will reload fresh data")
