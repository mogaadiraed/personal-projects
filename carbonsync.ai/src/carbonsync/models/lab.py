"""Model lab: run Prophet, Chronos-2 and Ens-PC on any training window / origin / horizon.

An *experiment* trains on ``train_days`` days ending at ``origin`` and forecasts
``horizon_days`` days after it. Where the forecast overlaps observed data it is
scored (MAE, MASE, coverage, interval width, bias, total error) against the
actuals and against a seasonal-naive baseline (same weekday last week).

Ens-PC is the average of the Prophet and Chronos-2 forecasts, so when it is
requested together with them the two component forecasts are reused rather than
recomputed – exactly how the production ensemble is built.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from carbonsync.backtest.metrics import evaluate_forecast
from carbonsync.obs import get_logger

log = get_logger(__name__)

MODELS = ("prophet", "chronos", "ens_pc")
LABELS = {"prophet": "Prophet", "chronos": "Chronos-2", "ens_pc": "Ens-PC", "naive": "Seasonal naive"}


@dataclass
class ModelRun:
    model: str
    forecast: pd.DataFrame
    fit_seconds: float
    predict_seconds: float
    metrics: dict[str, float] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class Experiment:
    origin: pd.Timestamp
    train: pd.DataFrame
    actual: pd.DataFrame  # observed days inside the horizon (may be empty)
    horizon_days: int
    runs: dict[str, ModelRun]
    naive: ModelRun | None
    internal: dict[str, ModelRun] = field(default_factory=dict)  # every model computed (Ens-PC parts too)

    @property
    def evaluated_days(self) -> int:
        return len(self.actual)


def _score(train: pd.DataFrame, actual: pd.DataFrame, fc: pd.DataFrame) -> dict[str, float]:
    if actual.empty:
        return {}
    m = actual[["date", "co2e_total_t"]].merge(fc, on="date", how="inner")
    if m.empty:
        return {}
    y, p = m["co2e_total_t"].to_numpy(float), m["yhat"].to_numpy(float)
    out = evaluate_forecast(train["co2e_total_t"].to_numpy(float), y, p,
                            m["lo90"].to_numpy(float), m["hi90"].to_numpy(float))
    out["bias"] = float(np.mean(p - y))
    out["total_error_pct"] = float((p.sum() - y.sum()) / y.sum() * 100) if y.sum() else float("nan")
    out["days"] = len(m)
    return out


def seasonal_naive(train: pd.DataFrame, horizon_days: int) -> pd.DataFrame:
    """Repeat the last observed week; band = ±1.645 σ of weekly differences."""
    y = train["co2e_total_t"].to_numpy(float)
    last_week = y[-7:]
    yhat = np.resize(last_week, horizon_days)
    diffs = y[7:] - y[:-7] if len(y) > 14 else np.array([y.std() if len(y) else 1.0])
    hw = 1.645 * float(np.nanstd(diffs))
    dates = pd.date_range(train["date"].max() + pd.Timedelta(days=1), periods=horizon_days, freq="D")
    return pd.DataFrame({"date": dates, "horizon": np.arange(1, horizon_days + 1), "yhat": yhat,
                         "lo90": np.clip(yhat - hw, 0, None), "hi90": yhat + hw, "model_name": "naive"})


def run_experiment(
    df: pd.DataFrame,
    models: list[str],
    origin: pd.Timestamp,
    train_days: int | None,
    horizon_days: int,
) -> Experiment:
    """Run the requested models; logs every step under ``carbonsync.models.lab``."""
    from carbonsync.models.chronos_model import ChronosForecaster
    from carbonsync.models.prophet_model import ProphetForecaster

    df = df.sort_values("date").reset_index(drop=True)
    origin = pd.Timestamp(origin)
    history = df[df["date"] <= origin]
    train = history if train_days is None else history.tail(train_days)
    if len(train) < 30:
        raise ValueError(f"Only {len(train)} days of training data before {origin.date()} – need at least 30.")
    end = origin + pd.Timedelta(days=horizon_days)
    actual = df[(df["date"] > origin) & (df["date"] <= end)]

    log.info("━━ Model lab ━━ train %s → %s (%d days) · horizon %d days · %d observed days to score against",
             train["date"].min().date(), train["date"].max().date(), len(train), horizon_days, len(actual))

    wanted = set(models)
    need_prophet = bool(wanted & {"prophet", "ens_pc"})
    need_chronos = bool(wanted & {"chronos", "ens_pc"})
    runs: dict[str, ModelRun] = {}

    if need_prophet:
        m = ProphetForecaster()
        t0 = time.perf_counter()
        m.fit(train)
        t1 = time.perf_counter()
        fc = m.predict(horizon_days=horizon_days)
        t2 = time.perf_counter()
        details: dict[str, Any] = {"backend": m.backend}
        raw = m.last_forecast_raw
        if raw is not None and m.model is not None:
            # In-sample decomposition + horizon, so the page can show what Prophet learned
            try:
                fitted = m.model.predict(m.model.history[["ds", *m.regressor_cols]])
                raw = pd.concat([fitted.assign(part="fit"), raw.assign(part="forecast")], ignore_index=True)
            except (ValueError, KeyError):
                raw = raw.assign(part="forecast")
            comp = pd.DataFrame({"date": pd.to_datetime(raw["ds"]), "part": raw["part"],
                                 "yhat": raw["yhat"], "trend": raw["trend"]})
            for c in ("weekly", "yearly", "extra_regressors_additive"):
                if c in raw.columns:
                    comp[c] = raw[c]
            details["components"] = comp
        runs["prophet"] = ModelRun("prophet", fc, t1 - t0, t2 - t1, details=details)
        log.info("Prophet (%s): fit %.2f s · predict %.2f s", m.backend, t1 - t0, t2 - t1)

    if need_chronos:
        m = ChronosForecaster()
        t0 = time.perf_counter()
        m.fit(train)
        t1 = time.perf_counter()
        fc = m.predict(horizon_days=horizon_days)
        t2 = time.perf_counter()
        runs["chronos"] = ModelRun("chronos", fc, t1 - t0, t2 - t1,
                                   details={"context_days": len(train)})
        log.info("Chronos-2: context prepared %.2f s · inference %.2f s", t1 - t0, t2 - t1)

    if "ens_pc" in wanted:
        p, c = runs["prophet"].forecast, runs["chronos"].forecast
        n = min(len(p), len(c))
        fc = pd.DataFrame({
            "date": p["date"].iloc[:n].to_numpy(),
            "horizon": np.arange(1, n + 1),
            "yhat": (p["yhat"].iloc[:n].to_numpy() + c["yhat"].iloc[:n].to_numpy()) / 2,
            "lo90": (p["lo90"].iloc[:n].to_numpy() + c["lo90"].iloc[:n].to_numpy()) / 2,
            "hi90": (p["hi90"].iloc[:n].to_numpy() + c["hi90"].iloc[:n].to_numpy()) / 2,
            "model_name": "ens_pc",
        })
        runs["ens_pc"] = ModelRun("ens_pc", fc, runs["prophet"].fit_seconds + runs["chronos"].fit_seconds,
                                  runs["prophet"].predict_seconds + runs["chronos"].predict_seconds,
                                  details={"components": {"prophet": p, "chronos": c}})
        log.info("Ens-PC: averaged Prophet and Chronos-2 (equal weights)")

    # Keep only what the user asked for; the Ens-PC parts stay reachable via ``internal``
    internal = dict(runs)
    runs = {k: v for k, v in runs.items() if k in wanted}

    naive = ModelRun("naive", seasonal_naive(train, horizon_days), 0.0, 0.0)
    for r in [*internal.values(), naive]:
        r.forecast["date"] = pd.to_datetime(r.forecast["date"])
        r.metrics = _score(train, actual, r.forecast)

    for k, r in runs.items():
        if r.metrics:
            log.info("Score %-9s MAE %.3f · MASE %.3f · coverage %.0f%% · total error %+.1f%%",
                     LABELS[k], r.metrics["mae"], r.metrics["mase"], r.metrics["coverage"] * 100,
                     r.metrics["total_error_pct"])
    if naive.metrics:
        log.info("Score %-9s MAE %.3f (reference)", "naive", naive.metrics["mae"])
    if actual.empty:
        log.info("Horizon is entirely after the last observed day – pure forecast, nothing to score yet")

    return Experiment(origin, train, actual, horizon_days, runs, naive, internal)
