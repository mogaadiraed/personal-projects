"""
CarbonSync AI – Model Ensemble (Ens-PC) & Router
=================================================
Router and ensemble:
  - Router (``router`` in config.yaml): the model the pipeline and "Auto" use. Default Chronos-2, the most
    accurate and best-calibrated model in the rolling backtest (MASE 0.62-0.67, ~82 % coverage of the 90 %
    interval, vs 0.88-0.94 and 35-48 % for Ens-PC). Prophet is the fallback when Chronos-2 cannot run.
    ``router_rule()`` is the one place the rule is described (pages, reports, assistant).
  - EnsPCForecaster: Average of Prophet and Chronos-2 forecasts
  - Stores forecasts and audit run records in DuckDB
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from carbonsync.db import connect as db_connect
from carbonsync.models.chronos_model import ChronosForecaster
from carbonsync.models.prophet_model import ProphetForecaster
from carbonsync.models.router import (  # noqa: F401 - re-exported for older imports
    MODEL_LABELS,
    MODELS,
    ROUTER_DEFAULTS,
    choose_model,
    router_rule,
    router_settings,
)
from carbonsync.obs import get_logger, step

log = get_logger(__name__)


def get_project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def load_config(config_path: Path | None = None) -> dict[str, Any]:
    if config_path is None:
        config_path = get_project_root() / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class EnsPCForecaster:
    """Ensemble of Prophet and Chronos-2 (Ens-PC)."""

    def __init__(self, interval_width: float = 0.90):
        self.interval_width = interval_width
        self.prophet = ProphetForecaster(interval_width=interval_width)
        self.chronos = ChronosForecaster()
        self.prophet_pred: pd.DataFrame | None = None
        self.chronos_pred: pd.DataFrame | None = None

    def fit(self, train_df: pd.DataFrame, target_col: str = "co2e_total_t") -> EnsPCForecaster:
        """Fits both Prophet and Chronos on context data."""
        self.prophet.fit(train_df, target_col=target_col)
        self.chronos.fit(train_df, target_col=target_col)
        return self

    def predict(
        self,
        horizon_days: int = 183,
        future_covariates: pd.DataFrame | None = None,
    ) -> pd.DataFrame:
        """Computes simple mean of Prophet and Chronos-2 predictions."""
        p_fc = self.prophet.predict(horizon_days=horizon_days, future_covariates=future_covariates)
        c_fc = self.chronos.predict(horizon_days=horizon_days, future_covariates=future_covariates)

        self.prophet_pred = p_fc
        self.chronos_pred = c_fc

        log.info("Ens-PC: averaging Prophet and Chronos-2 forecasts (equal weights)")
        min_len = min(len(p_fc), len(c_fc))
        p_fc = p_fc.iloc[:min_len]
        c_fc = c_fc.iloc[:min_len]

        ens_yhat = (p_fc["yhat"].values + c_fc["yhat"].values) / 2.0
        ens_lo90 = (p_fc["lo90"].values + c_fc["lo90"].values) / 2.0
        ens_hi90 = (p_fc["hi90"].values + c_fc["hi90"].values) / 2.0

        out = pd.DataFrame(
            {
                "date": p_fc["date"].values,
                "horizon": np.arange(1, min_len + 1),
                "yhat": np.clip(ens_yhat, 0.0, None),
                "lo90": np.clip(ens_lo90, 0.0, None),
                "hi90": np.clip(ens_hi90, 0.0, None),
                "model_name": "ens_pc",
            }
        )
        return out


def run_and_save_forecast(
    history_days: int | None = None,
    model_name: str = "auto",
    horizon_days: int = 183,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """Runs chosen model, generates forecast, and saves into DuckDB tables."""
    root = get_project_root()
    cfg = load_config(config_path)

    db_path = root / cfg.get("paths", {}).get("duckdb", "data/carbonsync.duckdb")

    conn = db_connect(str(db_path))
    df_emissions = conn.execute("SELECT * FROM emissions_daily ORDER BY date").fetchdf()

    if len(df_emissions) == 0:
        conn.close()
        raise ValueError("emissions_daily table is empty in DuckDB.")

    # Determine history slice
    if history_days is not None and history_days < len(df_emissions):
        train_df = df_emissions.tail(history_days).copy()
    else:
        train_df = df_emissions.copy()
        history_days = len(train_df)

    # Route model if auto
    routed = model_name == "auto"
    if routed:
        model_name = choose_model(history_days, cfg)
        log.info("Router: %d days of history → %s (%s)", history_days, MODEL_LABELS[model_name],
                 router_rule(cfg)["en"])
    if model_name not in MODELS:
        raise ValueError(f"Unknown model name '{model_name}'. Choose prophet, chronos, or ens_pc.")

    def _make(name: str):
        return {"prophet": ProphetForecaster, "chronos": ChronosForecaster, "ens_pc": EnsPCForecaster}[name]()

    # Fit & Predict (the router falls back when its model cannot run, e.g. Chronos-2 weights missing)
    try:
        with step(log, f"Forecast {model_name}: fit on {len(train_df)} days, predict {horizon_days} days"):
            model = _make(model_name)
            model.fit(train_df, target_col="co2e_total_t")
            forecast_df = model.predict(horizon_days=horizon_days)
    except Exception as err:
        fallback = router_settings(cfg)["fallback"]
        if not routed or fallback == model_name:
            conn.close()
            raise
        log.warning("Router: %s failed (%s) → fallback %s", MODEL_LABELS[model_name], err, MODEL_LABELS[fallback])
        model_name = fallback
        with step(log, f"Forecast {model_name} (fallback): fit on {len(train_df)} days, predict {horizon_days} days"):
            model = _make(model_name)
            model.fit(train_df, target_col="co2e_total_t")
            forecast_df = model.predict(horizon_days=horizon_days)

    run_id = f"{model_name}_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
    start_date = train_df["date"].min()
    end_date = train_df["date"].max()
    now_ts = datetime.now(UTC)

    # Store run record
    cursor = conn.cursor()
    params = json.dumps({"history_days": history_days, "horizon_days": horizon_days, "model": model_name})
    cursor.execute(
        """
        INSERT INTO forecast_runs (run_id, model_name, history_days, horizon_days, start_date, end_date, created_at, parameters)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [run_id, model_name, history_days, horizon_days, start_date, end_date, now_ts, params],
    )

    # Store forecast rows
    forecast_df["run_id"] = run_id
    cursor.register("fc_view", forecast_df)
    cursor.execute(
        """
        INSERT INTO forecasts (run_id, date, horizon, yhat, lo90, hi90, model_name)
        SELECT run_id, date, horizon, yhat, lo90, hi90, model_name FROM fc_view
        """
    )
    cursor.unregister("fc_view")
    cursor.close()
    conn.close()
    log.info("Saved forecast run %s (%d rows) to DuckDB", run_id, len(forecast_df))

    return {
        "run_id": run_id,
        "model_name": model_name,
        "history_days": history_days,
        "horizon_days": horizon_days,
        "mean_yhat": float(forecast_df["yhat"].mean()),
        "first_prediction": float(forecast_df["yhat"].iloc[0]),
        "last_prediction": float(forecast_df["yhat"].iloc[-1]),
    }


if __name__ == "__main__":
    print("Running forecast engine...")
    res = run_and_save_forecast(history_days=730, model_name="auto", horizon_days=183)
    print(
        f"Forecast complete [run_id={res['run_id']}]: Model '{res['model_name']}', "
        f"Horizon {res['horizon_days']} days, Mean daily CO2e {res['mean_yhat']:.2f} tCO2e."
    )
