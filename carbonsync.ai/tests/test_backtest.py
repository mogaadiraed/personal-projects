"""
Unit Tests for CarbonSync Backtest Framework & Metrics
======================================================
"""

import json
from pathlib import Path

import duckdb
import numpy as np
import pytest

from carbonsync.backtest.metrics import (
    calc_coverage,
    calc_mae,
    calc_mase,
    calc_mean_width,
    evaluate_forecast,
)
from carbonsync.ingest.load import get_duckdb_path


@pytest.fixture(scope="module")
def project_root():
    return Path(__file__).resolve().parents[1]


def test_mae_and_mase_metrics():
    """Checks mathematical correctness of MAE and seasonal-scaled MASE."""
    y_true = np.array([10.0, 12.0, 14.0, 16.0])
    y_pred = np.array([11.0, 11.0, 15.0, 18.0])

    # Absolute errors: [1.0, 1.0, 1.0, 2.0] -> mean = 1.25
    mae = calc_mae(y_true, y_pred)
    assert abs(mae - 1.25) < 1e-4

    # Dummy train series with constant seasonality
    y_train = np.array([10.0, 12.0, 10.0, 12.0, 10.0, 12.0, 10.0, 12.0, 10.0, 12.0])
    mase = calc_mase(y_train, y_true, y_pred, seasonality=2)
    assert mase > 0


def test_coverage_and_width():
    """Checks empirical coverage calculation and interval width."""
    y_true = np.array([5.0, 10.0, 15.0, 20.0])
    lo90 = np.array([4.0, 8.0, 14.0, 18.0])
    hi90 = np.array([6.0, 12.0, 16.0, 22.0])

    cov = calc_coverage(y_true, lo90, hi90)
    assert cov == 1.0

    width = calc_mean_width(lo90, hi90)
    assert abs(width - 3.0) < 1e-4

    full_eval = evaluate_forecast(
        y_train=np.arange(14),
        y_true=y_true,
        y_pred=(lo90 + hi90) / 2.0,
        lo90=lo90,
        hi90=hi90,
    )
    assert "mae" in full_eval
    assert "mase" in full_eval
    assert "coverage" in full_eval
    assert "mean_width" in full_eval


def test_backtest_duckdb_records_populated():
    """Checks that DuckDB contains the rolling backtest records across models."""
    db_path = get_duckdb_path()
    conn = duckdb.connect(str(db_path), read_only=True)

    count = conn.execute("SELECT COUNT(*) FROM backtest_results").fetchone()[0]
    assert count >= 10, f"Expected at least 10 backtest records, found {count}"

    models = [r[0] for r in conn.execute("SELECT DISTINCT model_name FROM backtest_results").fetchall()]
    for expected in ["seasonal_naive", "oracle", "prophet", "chronos", "ens_pc"]:
        assert expected in models, f"Model {expected} missing in backtest_results"

    # All MAE and MASE must be positive
    stats = conn.execute("SELECT MIN(mae), MIN(mase) FROM backtest_results").fetchone()
    assert stats[0] > 0
    assert stats[1] > 0

    conn.close()


def test_backtest_summary_file_exists(project_root):
    """Verifies that data/backtest_summary.json exists with summary metrics and metadata."""
    summary_path = project_root / "data" / "backtest_summary.json"
    assert summary_path.exists()

    with open(summary_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    results = data.get("results", data) if isinstance(data, dict) else data
    assert len(results) >= 5
    for item in results:
        assert "model_name" in item
        assert "mae" in item
        assert "mase" in item
