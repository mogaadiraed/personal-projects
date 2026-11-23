"""
CarbonSync AI – Chronos-2 Forecasting Wrapper
===============================================
Wraps Amazon Chronos-2 zero-shot foundation model for time series forecasting.
Features:
  - Pretrained foundation model (120M parameters)
  - Multi-quantile prediction (0.05, 0.50, 0.95) for 90% confidence bands
  - CPU inference with cached pipeline
  - Offline support via local cache
"""

from __future__ import annotations

import os
import time

import numpy as np
import pandas as pd

from carbonsync.obs import get_logger

log = get_logger(__name__)

# Global cached pipeline
_CHRONOS_PIPELINE = None


def get_chronos_pipeline(model_id: str = "amazon/chronos-2", device: str = "cpu"):
    """Loads and caches Chronos2Pipeline."""
    global _CHRONOS_PIPELINE
    if _CHRONOS_PIPELINE is None:
        log.info("Chronos-2: loading %s weights on %s (first use, ~20–40 s)…", model_id, device)
        t0 = time.perf_counter()
        # Prefer offline mode if already cached
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        from chronos import Chronos2Pipeline

        try:
            _CHRONOS_PIPELINE = Chronos2Pipeline.from_pretrained(model_id, device_map=device)
        except (OSError, RuntimeError, ValueError):
            # Fallback to online if local fetch fails
            os.environ.pop("HF_HUB_OFFLINE", None)
            _CHRONOS_PIPELINE = Chronos2Pipeline.from_pretrained(model_id, device_map=device)
        log.info("Chronos-2: weights loaded (%.1f s)", time.perf_counter() - t0)
    return _CHRONOS_PIPELINE


class ChronosForecaster:
    """Chronos-2 foundation forecasting model wrapper."""

    def __init__(self, model_id: str = "amazon/chronos-2", device: str = "cpu"):
        self.model_id = model_id
        self.device = device
        self.context_df: pd.DataFrame | None = None

    def fit(self, train_df: pd.DataFrame, target_col: str = "co2e_total_t") -> ChronosForecaster:
        """Stores context data for zero-shot forecasting."""
        df = train_df.copy()
        df["timestamp"] = pd.to_datetime(df["date"])
        df["target"] = df[target_col].astype(float)
        df["item_id"] = "factory_co2e"

        # Interpolate internal NaNs in context
        df["target"] = df["target"].interpolate(method="linear").bfill().ffill()
        self.context_df = df[["item_id", "timestamp", "target"]].sort_values("timestamp").reset_index(drop=True)
        return self

    def predict(
        self,
        horizon_days: int = 183,
        future_covariates: pd.DataFrame | None = None,
    ) -> pd.DataFrame:
        """Runs zero-shot quantile prediction for horizon_days."""
        if self.context_df is None or len(self.context_df) == 0:
            raise ValueError("ChronosForecaster must be fitted with context data first.")

        pipeline = get_chronos_pipeline(self.model_id, self.device)

        # Context dataframe
        last_date = self.context_df["timestamp"].max()
        future_dates = pd.date_range(last_date + pd.Timedelta(days=1), periods=horizon_days, freq="D")

        log.info("Chronos-2 zero-shot inference: context %d days → %d-day horizon, quantiles 5/50/95 %%",
                 len(self.context_df), horizon_days)
        t0 = time.perf_counter()
        # Call predict_df
        pred_df = pipeline.predict_df(
            df=self.context_df,
            prediction_length=horizon_days,
            quantile_levels=[0.05, 0.50, 0.95],
            id_column="item_id",
            timestamp_column="timestamp",
            target="target",
        )

        # Extract predictions
        # pred_df columns typically: item_id, timestamp, target, 0.05, 0.5, 0.95 or similar
        cols = pred_df.columns
        q05_col = next((c for c in ["0.05", 0.05] if c in cols), cols[-3])
        q50_col = next((c for c in ["0.5", "0.50", 0.5] if c in cols), cols[-2])
        q95_col = next((c for c in ["0.95", 0.95] if c in cols), cols[-1])

        yhat = np.clip(pred_df[q50_col].values[:horizon_days].astype(float), 0.0, None)
        lo90 = np.clip(pred_df[q05_col].values[:horizon_days].astype(float), 0.0, None)
        hi90 = np.clip(pred_df[q95_col].values[:horizon_days].astype(float), 0.0, None)

        log.info("Chronos-2 inference done (%.2f s) → mean %.2f t/day", time.perf_counter() - t0, float(yhat.mean()))
        out = pd.DataFrame(
            {
                "date": future_dates[: len(yhat)],
                "horizon": np.arange(1, len(yhat) + 1),
                "yhat": yhat,
                "lo90": lo90,
                "hi90": hi90,
                "model_name": "chronos",
            }
        )
        return out
