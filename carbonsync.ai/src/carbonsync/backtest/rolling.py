"""
CarbonSync AI – Rolling-Origin Backtesting Framework
======================================================
Implements rolling-origin backtesting protocol (Section 8.4):
  - Origins every 30 days starting from 6 months of data
  - Fixed history windows: 6, 12, 24 months
  - 183-day forecast horizon at each origin
  - Benchmarks against:
      * Seasonal-naive baseline (same weekday last week)
      * Oracle floor (from truth.parquet ground truth)
      * Prophet
      * Chronos-2
      * Ens-PC
  - Saves origin evaluations into DuckDB table `backtest_results`
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
import yaml

from carbonsync.backtest.metrics import evaluate_forecast
from carbonsync.models.chronos_model import ChronosForecaster
from carbonsync.models.prophet_model import ProphetForecaster


def get_project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def load_config(config_path: Path | None = None) -> dict[str, Any]:
    if config_path is None:
        config_path = get_project_root() / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def run_rolling_backtest(
    max_origins: int = 5,
    history_lengths_months: list[int] | None = None,
    horizon_days: int = 183,
    config_path: Path | None = None,
) -> pd.DataFrame:
    """Executes rolling-origin backtest across history lengths and models."""
    root = get_project_root()
    cfg = load_config(config_path)

    bt_cfg = cfg.get("backtest", {})
    if history_lengths_months is None:
        history_lengths_months = bt_cfg.get("history_lengths_months", [6, 12, 24])

    db_path = root / cfg.get("paths", {}).get("duckdb", "data/carbonsync.duckdb")
    truth_path = root / cfg.get("paths", {}).get("truth", "data/truth") / "truth.parquet"

    conn = duckdb.connect(str(db_path))
    df_daily = conn.execute("SELECT * FROM emissions_daily ORDER BY date").fetchdf()
    df_daily["date"] = pd.to_datetime(df_daily["date"])

    # Load ground truth for Oracle baseline
    has_truth = truth_path.exists()
    if has_truth:
        df_truth = pd.read_parquet(truth_path)
        df_truth["date"] = pd.to_datetime(df_truth["date"])
    else:
        df_truth = df_daily.copy()

    total_days = len(df_daily)
    min_start_idx = 24 * 30  # start after 24 months so all window lengths are available

    # Pick rolling origin indices (every 30 days, working backwards or forward)
    step = int(bt_cfg.get("origin_step_days", 30))
    possible_origins = list(range(min_start_idx, total_days - horizon_days, step))

    if len(possible_origins) > max_origins:
        # Pick evenly spaced origins across available timeline
        indices = np.linspace(0, len(possible_origins) - 1, max_origins, dtype=int)
        selected_origins = [possible_origins[i] for i in indices]
    else:
        selected_origins = possible_origins

    results: list[dict[str, Any]] = []

    for orig_idx in selected_origins:
        origin_date = df_daily.iloc[orig_idx]["date"]
        test_df = df_daily.iloc[orig_idx : orig_idx + horizon_days].copy()
        y_test = test_df["co2e_total_t"].values

        # Oracle test values (clean ground truth)
        truth_test = df_truth[(df_truth["date"] >= test_df["date"].min()) & (df_truth["date"] <= test_df["date"].max())]
        y_oracle = truth_test["co2e_total_t"].values[: len(y_test)]

        for months in history_lengths_months:
            hist_days = months * 30
            start_idx = max(0, orig_idx - hist_days)
            train_df = df_daily.iloc[start_idx:orig_idx].copy()
            y_train = train_df["co2e_total_t"].values

            # 1. Seasonal-Naive Baseline (same weekday of previous week)
            clean_train = pd.Series(y_train).interpolate().bfill().ffill().values
            last_week = clean_train[-7:]
            # Repeat last week across horizon
            reps = int(np.ceil(horizon_days / 7))
            y_naive = np.tile(last_week, reps)[:horizon_days]
            std_naive = float(np.nanstd(clean_train))
            if std_naive <= 1e-4:
                std_naive = 1.0
            lo_naive = np.clip(y_naive - 1.645 * std_naive, 0.0, None)
            hi_naive = np.clip(y_naive + 1.645 * std_naive, 0.0, None)

            m_naive = evaluate_forecast(y_train, y_test, y_naive, lo_naive, hi_naive)
            results.append(
                {
                    "origin_date": origin_date.strftime("%Y-%m-%d"),
                    "model_name": "seasonal_naive",
                    "history_months": months,
                    "horizon_days": horizon_days,
                    **m_naive,
                }
            )

            # 2. Oracle Baseline (noise-free floor)
            if len(y_oracle) == len(y_test):
                std_oracle = np.nanstd(y_oracle) * 0.2
                lo_oracle = np.clip(y_oracle - 1.645 * std_oracle, 0.0, None)
                hi_oracle = np.clip(y_oracle + 1.645 * std_oracle, 0.0, None)
                m_oracle = evaluate_forecast(y_train, y_test, y_oracle, lo_oracle, hi_oracle)
                results.append(
                    {
                        "origin_date": origin_date.strftime("%Y-%m-%d"),
                        "model_name": "oracle",
                        "history_months": months,
                        "horizon_days": horizon_days,
                        **m_oracle,
                    }
                )

            # 3. Prophet Model
            p_pred = None
            try:
                p_model = ProphetForecaster()
                p_model.fit(train_df, target_col="co2e_total_t")
                p_pred = p_model.predict(horizon_days=horizon_days)
                m_prophet = evaluate_forecast(
                    y_train,
                    y_test,
                    p_pred["yhat"].values,
                    p_pred["lo90"].values,
                    p_pred["hi90"].values,
                )
                results.append(
                    {
                        "origin_date": origin_date.strftime("%Y-%m-%d"),
                        "model_name": "prophet",
                        "history_months": months,
                        "horizon_days": horizon_days,
                        **m_prophet,
                    }
                )
            except (OSError, RuntimeError, ValueError) as err:
                logging.getLogger("backtest").warning("Prophet backtest origin failed: %s", err)

            # 4. Chronos-2 Model (every window: it is the router's model, so 6 months needs evidence too)
            try:
                c_model = ChronosForecaster()
                c_model.fit(train_df, target_col="co2e_total_t")
                c_pred = c_model.predict(horizon_days=horizon_days)
                m_chronos = evaluate_forecast(
                    y_train,
                    y_test,
                    c_pred["yhat"].values,
                    c_pred["lo90"].values,
                    c_pred["hi90"].values,
                )
                results.append(
                    {
                        "origin_date": origin_date.strftime("%Y-%m-%d"),
                        "model_name": "chronos",
                        "history_months": months,
                        "horizon_days": horizon_days,
                        **m_chronos,
                    }
                )

                # 5. Ens-PC (Average of Prophet and Chronos)
                if p_pred is None:
                    continue
                ens_yhat = (p_pred["yhat"].values + c_pred["yhat"].values) / 2.0
                ens_lo = (p_pred["lo90"].values + c_pred["lo90"].values) / 2.0
                ens_hi = (p_pred["hi90"].values + c_pred["hi90"].values) / 2.0
                m_ens = evaluate_forecast(y_train, y_test, ens_yhat, ens_lo, ens_hi)
                results.append(
                    {
                        "origin_date": origin_date.strftime("%Y-%m-%d"),
                        "model_name": "ens_pc",
                        "history_months": months,
                        "horizon_days": horizon_days,
                        **m_ens,
                    }
                )
            except (OSError, RuntimeError, ValueError) as err:
                logging.getLogger("backtest").warning("Chronos backtest origin failed: %s", err)

    df_results = pd.DataFrame(results)

    # Store into DuckDB backtest_results table
    cursor = conn.cursor()
    cursor.execute("DELETE FROM backtest_results")
    cursor.register("bt_view", df_results)
    cursor.execute(
        """
        INSERT INTO backtest_results (origin_date, model_name, history_months, horizon_days, mae, mase, coverage, mean_width)
        SELECT origin_date::DATE, model_name, history_months, horizon_days, mae, mase, coverage, mean_width FROM bt_view
        """
    )
    cursor.unregister("bt_view")
    cursor.close()
    conn.close()

    # Save summary report JSON with R1 metadata
    summary_file = root / "data" / "backtest_summary.json"
    summary_stats = df_results.groupby(["model_name", "history_months"])[["mae", "mase", "coverage", "mean_width"]].mean().reset_index()
    records = summary_stats.to_dict(orient="records")
    from datetime import datetime

    means = df_results.groupby("model_name")["mase"].mean()
    ranked = [m for m in ("chronos", "ens_pc", "prophet") if m in means.index]
    best = min(ranked, key=lambda m: means[m]) if ranked else None
    output_data = {
        "meta": {
            "commit": _git_commit(root),
            "date": datetime.now().strftime("%Y-%m-%d"),
            "computed_at": datetime.now().isoformat(timespec="seconds"),
            "data_through": str(df_daily["date"].max().date()),
            "days": int(total_days),
            "origins": [df_daily.iloc[i]["date"].strftime("%Y-%m-%d") for i in selected_origins],
            "evaluations": len(df_results),
            "seed": 42,
            "protocol": f"rolling origin, {len(selected_origins)} cuts, history "
                        f"{', '.join(str(m) for m in history_lengths_months)} months, {horizon_days}-day horizon",
            "target": "co2e_total_t",
            "calibrated_against": "Tunisian industrial plant (synthetic benchmark)",
            "conformal_method": "split-conformal with horizon buckets",
            "note": (f"Recomputed on the data through {df_daily['date'].max().date()} with "
                     f"{len(selected_origins)} origins. Mean MASE: "
                     + ", ".join(f"{m} {means[m]:.3f}" for m in ranked) + f" (lowest: {best}).") if best else "",
        },
        "results": records,
    }
    with open(summary_file, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2)

    return df_results


def _git_commit(root: Path) -> str:
    """Short commit of the code that computed the backtest (provenance), or "unknown" outside git."""
    import subprocess

    try:
        def git(*args: str) -> str:
            return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=5,
                                  check=False).stdout.strip()

        commit = git("rev-parse", "--short", "HEAD")
        dirty = bool(git("status", "--porcelain", "--untracked-files=no"))
        return (commit + ("-dirty" if dirty else "")) if commit else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def refresh_backtest(config_path: Path | None = None) -> dict[str, Any]:
    """Recomputes the backtest on the current data (pipeline step after each upload)."""
    cfg = load_config(config_path)
    bt = cfg.get("backtest", {})
    res = run_rolling_backtest(max_origins=int(bt.get("max_origins", 12)),
                               horizon_days=int(bt.get("horizon_days", 183)), config_path=config_path)
    if res.empty:
        return {"evaluations": 0}
    means = res.groupby("model_name")["mase"].mean()
    models = [m for m in ("chronos", "ens_pc", "prophet") if m in means.index]
    best = min(models, key=lambda m: means[m]) if models else None
    return {"evaluations": len(res), "origins": int(res["origin_date"].nunique()),
            "best_model": best, "mase": {m: round(float(means[m]), 3) for m in means.index}}


if __name__ == "__main__":
    print("Running rolling-origin backtest protocol...")
    res = run_rolling_backtest(max_origins=int(load_config().get("backtest", {}).get("max_origins", 12)))
    print(f"Backtest completed: {len(res)} evaluations recorded across origins and models.")
    summary = res.groupby("model_name")[["mae", "mase", "coverage"]].mean().round(3)
    print("\nMean Performance Across Origins:")
    print(summary)
