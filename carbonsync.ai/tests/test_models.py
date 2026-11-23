"""
Unit Tests for CarbonSync Forecasting Engine & Models
======================================================
"""

import numpy as np
import pandas as pd

from carbonsync.ingest.load import get_duckdb_path
from carbonsync.models.conformal import (
    compute_conformal_halfwidths,
    evaluate_coverage,
)
from carbonsync.models.ensemble import choose_model
from carbonsync.models.prophet_model import ProphetForecaster


def test_router_logic():
    """Default router: Chronos-2 from 60 days of history, Prophet below (the backtest's best model)."""
    assert choose_model(30) == "prophet"
    assert choose_model(59) == "prophet"
    assert choose_model(60) == "chronos"
    assert choose_model(365) == "chronos"
    assert choose_model(3000) == "chronos"


def test_router_settings_and_legacy_config():
    from carbonsync.models.router import router_rule, router_settings

    custom = {"router": {"model": "ens_pc", "fallback": "prophet", "min_days": 200}}
    assert choose_model(199, custom) == "prophet" and choose_model(200, custom) == "ens_pc"
    # an old config.yaml (before the switch) keeps its rule: Prophet < 365 days <= Ens-PC
    legacy = {"router": {"min_days_for_ensemble": 365}}
    assert router_settings(legacy) == {"model": "ens_pc", "fallback": "prophet", "min_days": 365}
    assert choose_model(364, legacy) == "prophet" and choose_model(365, legacy) == "ens_pc"
    # unknown model names fall back to the defaults
    assert router_settings({"router": {"model": "gpt", "fallback": "x"}})["model"] == "chronos"
    rule = router_rule({}, 3012)
    assert rule["choice"] == "chronos" and "Chronos-2" in rule["en"] and "60" in rule["fr"]


def test_prophet_forecaster_fit_predict():
    """Checks ProphetForecaster fits and generates 30 days of bounded forecasts."""
    dates = pd.date_range("2023-01-01", periods=90, freq="D")
    1000 + 200 * np.sin(np.arange(90) * 2 * np.pi / 7)
    df = pd.DataFrame(
        {
            "date": dates,
            "co2e_total_t": 8.0 + 1.5 * np.sin(np.arange(90) * 2 * np.pi / 7),
            "is_weekend": (dates.dayofweek >= 5).astype(int),
            "is_planned_shutdown": 0,
            "solar_regime": 1,
            "efficiency_regime": 1,
        }
    )

    model = ProphetForecaster(interval_width=0.90)
    model.fit(df, target_col="co2e_total_t")
    pred = model.predict(horizon_days=30)

    assert len(pred) == 30
    assert (pred["yhat"] >= 0).all()
    assert (pred["lo90"] <= pred["hi90"]).all()
    assert "date" in pred.columns
    assert "horizon" in pred.columns


def test_conformal_halfwidth_and_coverage():
    """Checks finite-sample conformal quantile calculation and bucket smoothing."""
    # 20 backtest origins, 30 horizon days
    rng = np.random.default_rng(123)
    residuals = rng.normal(0, 1.0, size=(20, 30))
    abs_res = np.abs(residuals)

    halfwidths = compute_conformal_halfwidths(abs_res, alpha=0.10, smooth_buckets=True)
    assert len(halfwidths) == 30
    assert (halfwidths > 0).all()

    # Test coverage evaluation
    actuals = np.array([5.0, 6.0, 7.0, 8.0])
    lo90 = np.array([4.0, 5.5, 6.5, 7.5])
    hi90 = np.array([6.0, 6.5, 7.5, 8.5])
    metrics = evaluate_coverage(actuals, lo90, hi90)

    assert metrics["coverage"] == 1.0
    assert metrics["mean_width"] == 1.25


def test_forecast_stored_in_duckdb():
    """Checks that DuckDB contains precomputed forecasts and run logs."""
    import duckdb

    db_path = get_duckdb_path()
    conn = duckdb.connect(str(db_path), read_only=True)

    runs = conn.execute("SELECT COUNT(*) FROM forecast_runs").fetchone()[0]
    assert runs >= 1, "Expected at least 1 forecast run stored"

    fc_rows = conn.execute("SELECT COUNT(*) FROM forecasts").fetchone()[0]
    assert fc_rows >= 183, f"Expected at least 183 forecast rows, found {fc_rows}"

    sample = conn.execute("SELECT yhat, lo90, hi90 FROM forecasts LIMIT 5").fetchdf()
    assert (sample["yhat"] > 0).all()
    assert (sample["lo90"] <= sample["hi90"]).all()

    conn.close()
