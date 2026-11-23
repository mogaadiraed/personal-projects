"""
CarbonSync AI – Backtest Evaluation Metrics
=============================================
Calculates standardized evaluation metrics for factory CO2e forecasts:
  - MAE (Mean Absolute Error in tCO2e/day)
  - MASE (Mean Absolute Scaled Error vs weekly seasonal-naive baseline)
  - Coverage (Share of actual values within 90% prediction band)
  - Mean Width (Average interval width: hi90 - lo90)
"""

from __future__ import annotations

import numpy as np


def calc_mae(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    """Mean Absolute Error."""
    mask = (~np.isnan(y_true)) & (~np.isnan(y_pred))
    if not mask.any():
        return float(np.nan)
    return float(np.mean(np.abs(y_true[mask] - y_pred[mask])))


def calc_mase(
    y_train: np.ndarray,
    y_true: np.ndarray,
    y_pred: np.ndarray,
    seasonality: int = 7,
) -> float:
    """Mean Absolute Scaled Error relative to seasonal-naive baseline (same day of previous week)."""
    # In-sample seasonal naive MAE
    clean_train = y_train[~np.isnan(y_train)]
    if len(clean_train) <= seasonality:
        scale = 1.0
    else:
        diffs = np.abs(clean_train[seasonality:] - clean_train[:-seasonality])
        scale = float(np.mean(diffs))
        if scale <= 1e-4:
            scale = 1.0

    mae_pred = calc_mae(y_true, y_pred)
    return float(mae_pred / scale)


def calc_coverage(
    y_true: np.ndarray,
    lo90: np.ndarray,
    hi90: np.ndarray,
) -> float:
    """Empirical coverage (% of actual values within 90% band)."""
    mask = (~np.isnan(y_true)) & (~np.isnan(lo90)) & (~np.isnan(hi90))
    if not mask.any():
        return 0.0
    covered = (y_true[mask] >= lo90[mask]) & (y_true[mask] <= hi90[mask])
    return float(np.mean(covered))


def calc_mean_width(lo90: np.ndarray, hi90: np.ndarray) -> float:
    """Mean width of prediction interval."""
    mask = (~np.isnan(lo90)) & (~np.isnan(hi90))
    if not mask.any():
        return 0.0
    return float(np.mean(hi90[mask] - lo90[mask]))


def evaluate_forecast(
    y_train: np.ndarray,
    y_true: np.ndarray,
    y_pred: np.ndarray,
    lo90: np.ndarray,
    hi90: np.ndarray,
    seasonality: int = 7,
) -> dict[str, float]:
    """Computes full suite of backtesting metrics."""
    return {
        "mae": calc_mae(y_true, y_pred),
        "mase": calc_mase(y_train, y_true, y_pred, seasonality=seasonality),
        "coverage": calc_coverage(y_true, lo90, hi90),
        "mean_width": calc_mean_width(lo90, hi90),
    }
