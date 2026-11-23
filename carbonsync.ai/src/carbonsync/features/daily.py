"""
CarbonSync AI – Daily Feature Table & Emissions Calculation
=============================================================
Merges departmental raw tables from DuckDB into:
  1. `daily`: Unified daily table with calendar features and missingness imputation.
  2. `emissions_daily`: Activity metrics + Scope 1, Scope 2, other, and total CO2e emissions (tCO2e/day).
  3. Prepares tables for forecasting: `forecast_runs`, `forecasts`, `backtest_results`.

Missing Value Policy:
  - Short gaps (<= 3 consecutive days): Imputed via linear interpolation.
  - Imputed dates marked with `is_imputed = 1` (0 otherwise).
  - Long gaps (> 3 days): Left as NaN for model handling.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
import yaml

from carbonsync.obs import get_logger

log = get_logger(__name__)


def get_project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def load_config(config_path: Path | None = None) -> dict[str, Any]:
    if config_path is None:
        config_path = get_project_root() / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def build_daily_features(config_path: Path | None = None) -> dict[str, Any]:
    """Builds `daily` and `emissions_daily` tables in DuckDB."""
    root = get_project_root()
    cfg = load_config(config_path)

    gen_cfg = cfg.get("generator", {})
    ef_cfg = cfg.get("emission_factors", {})
    paths_cfg = cfg.get("paths", {})

    db_path = root / paths_cfg.get("duckdb", "data/carbonsync.duckdb")
    start_date = gen_cfg.get("start_date", "2018-01-01")
    end_date = gen_cfg.get("end_date", "2025-12-31")
    solar_start = pd.Timestamp(gen_cfg.get("solar_start", "2021-06-01"))
    eff_start = pd.Timestamp(gen_cfg.get("efficiency_start", "2023-03-01"))

    # Emission factors (kgCO2e per unit)
    ef_elec = float(ef_cfg.get("electricity_kgco2_per_kwh", 0.483))
    ef_fuel = float(ef_cfg.get("fuel_oil_kgco2_per_l", 2.68))
    ef_diesel = float(ef_cfg.get("diesel_kgco2_per_l", 2.68))
    ef_waste = float(ef_cfg.get("waste_kgco2_per_kg", 0.50))
    ef_water = float(ef_cfg.get("water_kgco2_per_m3", 0.30))

    conn = duckdb.connect(str(db_path))

    # 1. Fetch raw data from DuckDB tables
    df_energy = conn.execute("SELECT date, electricity_kwh, fuel_oil_l FROM raw_energy ORDER BY date").fetchdf()
    df_prod = conn.execute("SELECT date, units_produced, operating_hours, shutdown_flag FROM raw_production ORDER BY date").fetchdf()
    df_log = conn.execute("SELECT date, trips, diesel_l FROM raw_logistics ORDER BY date").fetchdf()
    df_ww = conn.execute("SELECT date, waste_kg, water_m3 FROM raw_waste_water ORDER BY date").fetchdf()

    # Convert dates to datetime64[ns]
    for df in [df_energy, df_prod, df_log, df_ww]:
        df["date"] = pd.to_datetime(df["date"])

    # 2. Build full continuous date spine.
    # It runs to the last day reported by *every* department, so newly uploaded months are
    # included automatically, and a month sent by only some departments waits for the others
    # instead of being filled with invented values.
    dept_last = {
        "energie": df_energy["date"].max(),
        "production": df_prod["date"].max(),
        "logistique": df_log["date"].max(),
        "dechets_eau": df_ww["date"].max(),
    }
    known_last = [d for d in dept_last.values() if pd.notna(d)]
    if known_last:
        # The configured baseline period is always kept (its gaps are imputed and flagged);
        # beyond it, the series grows only as far as every department has reported.
        end_date = max(pd.Timestamp(end_date), min(known_last))
    pending = {k: str(v.date()) for k, v in dept_last.items() if pd.notna(v) and v > pd.Timestamp(end_date)}
    if pending:
        log.warning("Data complete through %s; waiting for other departments before using: %s",
                    pd.Timestamp(end_date).date(), pending)
    spine_dates = pd.date_range(start_date, end_date, freq="D")
    df_daily = pd.DataFrame({"date": spine_dates})
    log.info("Date spine %s → %s (%d days)", spine_dates.min().date(), spine_dates.max().date(), len(spine_dates))

    # Merge departments onto continuous date spine
    df_daily = df_daily.merge(df_energy, on="date", how="left")
    df_daily = df_daily.merge(df_prod, on="date", how="left")
    df_daily = df_daily.merge(df_log, on="date", how="left")
    df_daily = df_daily.merge(df_ww, on="date", how="left")

    # 3. Imputation Logic (fill short gaps <= 3 days, track with is_imputed)
    feature_cols = [
        "electricity_kwh",
        "fuel_oil_l",
        "units_produced",
        "operating_hours",
        "trips",
        "diesel_l",
        "waste_kg",
        "water_m3",
    ]

    is_imputed = np.zeros(len(df_daily), dtype=int)

    for col in feature_cols:
        series = df_daily[col]
        # Identify gaps and perform robust interpolation to guarantee complete time series
        mask_na = series.isna()
        if mask_na.any():
            interpolated = series.interpolate(method="linear").bfill().ffill()
            # Mark imputed indices
            filled_mask = mask_na & (~interpolated.isna())
            is_imputed[filled_mask.values] = 1
            df_daily[col] = interpolated

    # Fill missing shutdown_flag (if units_produced <= 150 -> 1, else 0)
    if "shutdown_flag" in df_daily.columns:
        df_daily["shutdown_flag"] = df_daily["shutdown_flag"].fillna(
            (df_daily["units_produced"] < 150).astype(int)
        ).astype(int)

    df_daily["is_imputed"] = is_imputed

    # 4. Calendar & Operational Features
    dates = df_daily["date"]
    df_daily["day_of_week"] = dates.dt.dayofweek
    df_daily["is_weekend"] = (dates.dt.dayofweek >= 5).astype(int)
    df_daily["month"] = dates.dt.month
    df_daily["day_of_year"] = dates.dt.dayofyear

    # Planned shutdowns: August 1-14, Christmas Dec 24-31, Jan 1
    planned_aug = (dates.dt.month == 8) & (dates.dt.day <= 14)
    planned_xmas = ((dates.dt.month == 12) & (dates.dt.day >= 24)) | ((dates.dt.month == 1) & (dates.dt.day == 1))
    df_daily["is_planned_shutdown"] = (planned_aug | planned_xmas).astype(int)

    # Regime Shift Indicators
    df_daily["solar_regime"] = (dates >= solar_start).astype(int)
    df_daily["efficiency_regime"] = (dates >= eff_start).astype(int)

    # 5. Calculate Emissions (tCO2e/day)
    co2e_scope1 = (df_daily["fuel_oil_l"] * ef_fuel + df_daily["diesel_l"] * ef_diesel) / 1000.0
    co2e_scope2 = (df_daily["electricity_kwh"] * ef_elec) / 1000.0
    co2e_other = (df_daily["waste_kg"] * ef_waste + df_daily["water_m3"] * ef_water) / 1000.0
    co2e_total = co2e_scope1 + co2e_scope2 + co2e_other

    df_emissions = df_daily.copy()
    df_emissions["co2e_scope1_t"] = np.round(co2e_scope1, 4)
    df_emissions["co2e_scope2_t"] = np.round(co2e_scope2, 4)
    df_emissions["co2e_other_t"] = np.round(co2e_other, 4)
    df_emissions["co2e_total_t"] = np.round(co2e_total, 4)

    # 6. Save Tables in DuckDB
    cursor = conn.cursor()

    cursor.register("daily_view", df_daily)
    cursor.execute("CREATE OR REPLACE TABLE daily AS SELECT * FROM daily_view ORDER BY date")
    cursor.unregister("daily_view")

    cursor.register("emissions_view", df_emissions)
    cursor.execute("CREATE OR REPLACE TABLE emissions_daily AS SELECT * FROM emissions_view ORDER BY date")
    cursor.unregister("emissions_view")

    # 7. Initialize Forecasting and Backtest Tables
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS forecast_runs (
            run_id VARCHAR PRIMARY KEY,
            model_name VARCHAR,
            history_days INTEGER,
            horizon_days INTEGER,
            start_date DATE,
            end_date DATE,
            created_at TIMESTAMP,
            parameters VARCHAR
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS forecasts (
            run_id VARCHAR,
            date DATE,
            horizon INTEGER,
            yhat DOUBLE,
            lo90 DOUBLE,
            hi90 DOUBLE,
            model_name VARCHAR
        )
        """
    )

    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS backtest_results (
            origin_date DATE,
            model_name VARCHAR,
            history_months INTEGER,
            horizon_days INTEGER,
            mae DOUBLE,
            mase DOUBLE,
            coverage DOUBLE,
            mean_width DOUBLE
        )
        """
    )

    cursor.close()
    conn.close()

    imputed_days = int(df_daily["is_imputed"].sum())
    total_days = len(df_daily)
    imputed_pct = float(imputed_days / total_days)
    log.info("emissions_daily rebuilt: %d days · %d imputed (%.1f%%) · mean %.2f tCO2e/day",
             total_days, imputed_days, imputed_pct * 100, float(df_emissions["co2e_total_t"].mean()))

    return {
        "database": str(db_path.relative_to(root)),
        "total_days": total_days,
        "imputed_days": imputed_days,
        "imputed_pct": imputed_pct,
        "mean_daily_co2e_t": float(df_emissions["co2e_total_t"].mean()),
        "total_period_co2e_t": float(df_emissions["co2e_total_t"].sum()),
        "data_through": str(pd.Timestamp(end_date).date()),
        "department_last_dates": {k: (str(v.date()) if pd.notna(v) else None) for k, v in dept_last.items()},
        "pending_departments": pending,
    }


if __name__ == "__main__":
    print("Building daily feature table and calculating emissions in DuckDB...")
    res = build_daily_features()
    print(
        f"Feature tables built: {res['total_days']} days total, "
        f"{res['imputed_days']} imputed days ({res['imputed_pct']:.1%}). "
        f"Mean daily CO2e: {res['mean_daily_co2e_t']:.2f} tCO2e."
    )
