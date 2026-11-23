"""
CarbonSync AI – Split-Conformal Prediction Band
==================================================
Model-agnostic uncertainty estimation on backtest residuals (Section 8.5):
  - Finite-sample corrected quantile calculation
  - Horizon bucket smoothing (days 1-30, 31-90, 91-183) to prevent jagged bands
  - Empirical coverage verification against 90% nominal target
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def compute_conformal_halfwidths(
    abs_resid_by_h: np.ndarray,
    alpha: float = 0.10,
    smooth_buckets: bool = True,
) -> np.ndarray:
    """Computes conformal half-widths across horizon days with finite-sample correction.

    Parameters:
        abs_resid_by_h: 2D array of absolute residuals of shape (n_origins, horizon_days)
        alpha: significance level (0.10 for 90% confidence band)
        smooth_buckets: whether to smooth across horizon buckets (1-30, 31-90, 91-183)

    Returns:
        1D array of half-widths of shape (horizon_days,)
    """
    if abs_resid_by_h.ndim != 2:
        raise ValueError("abs_resid_by_h must be a 2D array of shape (n_origins, horizon_days)")

    n_origins, horizon = abs_resid_by_h.shape
    if n_origins == 0:
        return np.ones(horizon) * 1.5

    # Finite sample correction: q = min(1.0, ceil((n + 1) * (1 - alpha)) / n)
    q = min(1.0, float(np.ceil((n_origins + 1) * (1.0 - alpha)) / n_origins))
    raw_halfwidths = np.quantile(abs_resid_by_h, q, axis=0)

    if not smooth_buckets:
        return raw_halfwidths

    # Smooth by horizon buckets (1-30, 31-90, 91-183)
    smoothed = raw_halfwidths.copy()
    buckets = [(0, 30), (30, 90), (90, horizon)]
    for start, end in buckets:
        if start < horizon:
            actual_end = min(end, horizon)
            bucket_val = float(np.median(raw_halfwidths[start:actual_end]))
            smoothed[start:actual_end] = bucket_val

    # Ensure monotonic non-decreasing uncertainty
    for i in range(1, horizon):
        smoothed[i] = max(smoothed[i], smoothed[i - 1])

    return smoothed


def apply_conformal_band(
    forecast_df: pd.DataFrame,
    halfwidths: np.ndarray,
) -> pd.DataFrame:
    """Applies conformal halfwidths to a forecast dataframe."""
    df = forecast_df.copy()
    n = len(df)
    hw = halfwidths[:n]
    if len(hw) < n:
        # Extend with last available halfwidth
        pad = np.full(n - len(hw), hw[-1] if len(hw) > 0 else 1.5)
        hw = np.concatenate([hw, pad])

    df["lo90"] = np.clip(df["yhat"].values - hw, 0.0, None)
    df["hi90"] = np.clip(df["yhat"].values + hw, 0.0, None)
    return df


def evaluate_coverage(
    actuals: np.ndarray,
    lo90: np.ndarray,
    hi90: np.ndarray,
) -> dict[str, float]:
    """Calculates observed empirical coverage and mean interval width."""
    mask = ~np.isnan(actuals)
    if not mask.any():
        return {"coverage": 0.0, "mean_width": 0.0}

    y = actuals[mask]
    lo = lo90[mask]
    hi = hi90[mask]

    covered = (y >= lo) & (y <= hi)
    coverage = float(np.mean(covered))
    mean_width = float(np.mean(hi - lo))

    return {
        "coverage": coverage,
        "mean_width": mean_width,
    }
