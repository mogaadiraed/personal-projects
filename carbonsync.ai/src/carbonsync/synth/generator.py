"""
CarbonSync AI – Synthetic Data Generator
=========================================
Generates 8 years of daily synthetic factory data (2018-2025) for 4 departments:
  - Energy (electricity, fuel oil)
  - Production (units, operating hours, shutdowns)
  - Logistics (trips, diesel)
  - Waste & Water (waste, water)

Outputs:
  - data/truth/truth.parquet: Ground truth daily table with exact CO2e emissions (noise-free)
  - data/inbox/YYYY-MM/department CSVs: 384 monthly department CSV files with observation noise & gaps
  - data/synth_meta.json: Generation metadata (seed, parameters, event dates, effect sizes)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml


def get_project_root() -> Path:
    """Returns the carbonsync-dashboard project root directory."""
    return Path(__file__).resolve().parents[3]


def load_config(config_path: Path | None = None) -> dict[str, Any]:
    """Loads config.yaml from project root."""
    if config_path is None:
        config_path = get_project_root() / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def generate_synthetic_data(config_path: Path | None = None) -> dict[str, Any]:
    """Generates synthetic ground truth and monthly department CSVs."""
    root = get_project_root()
    cfg = load_config(config_path)

    gen_cfg = cfg.get("generator", {})
    ef_cfg = cfg.get("emission_factors", {})
    paths_cfg = cfg.get("paths", {})

    seed = gen_cfg.get("seed", 42)
    start_date = gen_cfg.get("start_date", "2018-01-01")
    end_date = gen_cfg.get("end_date", "2025-12-31")

    solar_start = pd.Timestamp(gen_cfg.get("solar_start", "2021-06-01"))
    solar_reduction = float(gen_cfg.get("solar_reduction", 0.15))
    eff_start = pd.Timestamp(gen_cfg.get("efficiency_start", "2023-03-01"))
    eff_reduction = float(gen_cfg.get("efficiency_reduction", 0.08))

    base_production = float(gen_cfg.get("base_production", 1000))
    annual_growth_rate = float(gen_cfg.get("annual_growth_rate", 0.03))
    weekend_factor = float(gen_cfg.get("weekend_factor", 0.35))
    seasonal_amplitude = float(gen_cfg.get("seasonal_amplitude", 0.08))
    shutdown_aug_days = int(gen_cfg.get("shutdown_aug_days", 14))
    fixed_elec_load = float(gen_cfg.get("fixed_elec_load", 6000))
    elec_per_production_unit = float(gen_cfg.get("elec_per_production_unit", 9))
    cooling_amplitude = float(gen_cfg.get("cooling_amplitude", 0.10))
    noise_std = float(gen_cfg.get("noise_std", 0.03))
    missing_rate = float(gen_cfg.get("missing_rate", 0.015))
    outlier_rate = float(gen_cfg.get("outlier_rate", 0.003))

    # Emission factors (kgCO2e per unit)
    ef_elec = float(ef_cfg.get("electricity_kgco2_per_kwh", 0.483))
    ef_fuel = float(ef_cfg.get("fuel_oil_kgco2_per_l", 2.68))
    ef_diesel = float(ef_cfg.get("diesel_kgco2_per_l", 2.68))
    ef_waste = float(ef_cfg.get("waste_kgco2_per_kg", 0.50))
    ef_water = float(ef_cfg.get("water_kgco2_per_m3", 0.30))

    # Output paths
    inbox_dir = root / paths_cfg.get("inbox", "data/inbox")
    truth_dir = root / paths_cfg.get("truth", "data/truth")
    inbox_dir.mkdir(parents=True, exist_ok=True)
    truth_dir.mkdir(parents=True, exist_ok=True)

    rng = np.random.default_rng(seed)

    # 1. Timeline
    days = pd.date_range(start_date, end_date, freq="D")
    n_days = len(days)
    t = np.arange(n_days)
    dow = days.dayofweek.values
    doy = days.dayofyear.values

    # 2. Production
    weekly = np.where(dow >= 5, weekend_factor, 1.0)
    yearly = 1.0 + seasonal_amplitude * np.sin(2 * np.pi * doy / 365.25)
    growth = 1.0 + annual_growth_rate * (t / 365.25)
    prod = base_production * weekly * yearly * growth

    shutdown = np.zeros(n_days, dtype=bool)
    # August closure
    shutdown |= (days.month == 8) & (days.day <= shutdown_aug_days)
    # Christmas & New Year break
    shutdown |= ((days.month == 12) & (days.day >= 24)) | ((days.month == 1) & (days.day == 1))

    # Random breakdowns (~6 per year, 1 to 4 days each)
    breakdowns_count = int(gen_cfg.get("random_breakdowns_per_year", 6) * (n_days / 365.25))
    breakdown_starts = rng.choice(n_days - 10, size=breakdowns_count, replace=False)
    for start in breakdown_starts:
        dur = rng.integers(
            gen_cfg.get("breakdown_duration_min", 1),
            gen_cfg.get("breakdown_duration_max", 4) + 1,
        )
        shutdown[start : min(start + dur, n_days)] = True

    # Drop production on shutdown days to 5-10%
    prod_drop = rng.uniform(0.05, 0.10, size=n_days)
    prod = np.where(shutdown, prod * prod_drop, prod)

    # Operating hours: 24h regular, reduced on weekends & shutdowns
    base_hours = np.where(dow >= 5, rng.uniform(8.0, 12.0, n_days), rng.uniform(22.0, 24.0, n_days))
    operating_hours = np.where(shutdown, rng.uniform(2.0, 5.0, n_days), base_hours)
    shutdown_flag = shutdown.astype(int)

    # 3. Energy: Electricity & Fuel Oil
    cooling = 1.0 + cooling_amplitude * np.clip(np.sin(2 * np.pi * (doy - 120) / 365.25), 0, None)
    elec = (fixed_elec_load + elec_per_production_unit * prod) * cooling * rng.normal(1.0, noise_std, n_days)

    # Regime shifts on electricity
    elec = np.where(days >= solar_start, elec * (1.0 - solar_reduction), elec)
    elec = np.where(days >= eff_start, elec * (1.0 - eff_reduction), elec)

    # Fuel oil: baseline winter heating + industrial heat proportional to production
    heating = 1.0 + 0.35 * np.clip(np.cos(2 * np.pi * doy / 365.25), 0, None)
    fuel_oil = (200.0 + 0.25 * prod) * heating * rng.normal(1.0, noise_std, n_days)
    fuel_oil = np.where(days >= eff_start, fuel_oil * (1.0 - eff_reduction * 0.5), fuel_oil)

    # 4. Logistics: Trips & Diesel
    trips_base = (prod / 50.0 + rng.normal(0, 1.5, n_days)) * weekly
    trips = np.round(np.clip(trips_base, 0, None)).astype(int)
    # Diesel litres proportional to trips + baseline site logistics
    diesel = (trips * 35.0 + 15.0) * rng.normal(1.0, noise_std, n_days)

    # 5. Waste & Water
    waste = (0.45 * prod + 100.0) * rng.normal(1.0, noise_std, n_days)
    water = (0.05 * prod + 20.0 * cooling) * rng.normal(1.0, noise_std, n_days)

    # 6. Calculate True Emissions (tCO2e/day) - Ground Truth
    co2e_scope1_t = (fuel_oil * ef_fuel + diesel * ef_diesel) / 1000.0
    co2e_scope2_t = (elec * ef_elec) / 1000.0
    co2e_other_t = (waste * ef_waste + water * ef_water) / 1000.0
    co2e_total_t = co2e_scope1_t + co2e_scope2_t + co2e_other_t

    # Save Ground Truth Parquet (noise-free, complete)
    df_truth = pd.DataFrame(
        {
            "date": days,
            "units_produced": np.round(prod, 1),
            "operating_hours": np.round(operating_hours, 1),
            "shutdown_flag": shutdown_flag,
            "electricity_kwh": np.round(elec, 2),
            "fuel_oil_l": np.round(fuel_oil, 2),
            "trips": trips,
            "diesel_l": np.round(diesel, 2),
            "waste_kg": np.round(waste, 2),
            "water_m3": np.round(water, 2),
            "co2e_scope1_t": np.round(co2e_scope1_t, 4),
            "co2e_scope2_t": np.round(co2e_scope2_t, 4),
            "co2e_other_t": np.round(co2e_other_t, 4),
            "co2e_total_t": np.round(co2e_total_t, 4),
        }
    )
    truth_file = truth_dir / "truth.parquet"
    df_truth.to_parquet(truth_file, index=False)

    # 7. Apply Observation Defects to Create Department Files
    # Copy data arrays for department exports
    obs_prod = df_truth["units_produced"].values.astype(float).copy()
    obs_hours = df_truth["operating_hours"].values.astype(float).copy()
    obs_shutdown = df_truth["shutdown_flag"].values.copy()

    obs_elec = df_truth["electricity_kwh"].values.copy()
    obs_fuel = df_truth["fuel_oil_l"].values.copy()

    obs_trips = df_truth["trips"].values.astype(float).copy()
    obs_diesel = df_truth["diesel_l"].values.copy()

    obs_waste = df_truth["waste_kg"].values.copy()
    obs_water = df_truth["water_m3"].values.copy()

    # Invalidate values with random missingness (~1.5%)
    for arr in [obs_prod, obs_hours, obs_elec, obs_fuel, obs_trips, obs_diesel, obs_waste, obs_water]:
        mask = rng.random(n_days) < missing_rate
        arr[mask] = np.nan

    # Invalidate with occasional multi-day gaps (department forgot to send 3-5 days)
    for arr in [obs_elec, obs_prod, obs_diesel, obs_waste]:
        gap_starts = rng.choice(n_days - 10, size=3, replace=False)
        for g_start in gap_starts:
            g_len = rng.integers(3, 6)
            arr[g_start : g_start + g_len] = np.nan

    # Inject minor typographical outliers (~0.3%)
    for arr in [obs_prod, obs_elec, obs_fuel, obs_diesel]:
        outlier_mask = rng.random(n_days) < outlier_rate
        multiplier = rng.choice([0.1, 10.0], size=n_days)
        arr[outlier_mask] = arr[outlier_mask] * multiplier[outlier_mask]

    # Department DataFrames
    df_energy = pd.DataFrame(
        {
            "date": days.strftime("%Y-%m-%d"),
            "electricity_kwh": obs_elec,
            "fuel_oil_l": obs_fuel,
        }
    )
    df_production = pd.DataFrame(
        {
            "date": days.strftime("%Y-%m-%d"),
            "units_produced": obs_prod,
            "operating_hours": obs_hours,
            "shutdown_flag": obs_shutdown,
        }
    )
    df_logistics = pd.DataFrame(
        {
            "date": days.strftime("%Y-%m-%d"),
            "trips": obs_trips,
            "diesel_l": obs_diesel,
        }
    )
    df_waste_water = pd.DataFrame(
        {
            "date": days.strftime("%Y-%m-%d"),
            "waste_kg": obs_waste,
            "water_m3": obs_water,
        }
    )

    # 8. Export Monthly CSV Files into data/inbox/
    # Month groups: YYYY-MM
    periods = days.to_period("M").unique()
    files_created = []

    for p in periods:
        month_str = str(p)
        mask = days.to_period("M") == p

        # Energy
        energy_path = inbox_dir / f"energie_{month_str}.csv"
        df_energy[mask].to_csv(energy_path, index=False)
        files_created.append(str(energy_path.relative_to(root)))

        # Production
        prod_path = inbox_dir / f"production_{month_str}.csv"
        df_production[mask].to_csv(prod_path, index=False)
        files_created.append(str(prod_path.relative_to(root)))

        # Logistics
        log_path = inbox_dir / f"logistique_{month_str}.csv"
        df_logistics[mask].to_csv(log_path, index=False)
        files_created.append(str(log_path.relative_to(root)))

        # Waste & Water
        ww_path = inbox_dir / f"dechets_eau_{month_str}.csv"
        df_waste_water[mask].to_csv(ww_path, index=False)
        files_created.append(str(ww_path.relative_to(root)))

    # 9. Save Generation Metadata
    meta = {
        "seed": seed,
        "start_date": start_date,
        "end_date": end_date,
        "total_days": n_days,
        "total_months": len(periods),
        "total_inbox_files": len(files_created),
        "departments": ["energie", "production", "logistique", "dechets_eau"],
        "regime_shifts": {
            "solar_contract": {
                "date": str(solar_start.date()),
                "electricity_reduction": solar_reduction,
            },
            "efficiency_plan": {
                "date": str(eff_start.date()),
                "electricity_reduction": eff_reduction,
                "fuel_reduction": eff_reduction * 0.5,
            },
        },
        "breakdowns_count": breakdowns_count,
        "missing_rate": missing_rate,
        "outlier_rate": outlier_rate,
        "truth_file": str(truth_file.relative_to(root)),
    }

    meta_file = root / "data" / "synth_meta.json"
    with open(meta_file, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)

    return meta


if __name__ == "__main__":
    print("Generating synthetic factory data...")
    res = generate_synthetic_data()
    print(
        f"Synthetic data generation complete: {res['total_days']} days, "
        f"{res['total_inbox_files']} inbox files generated, seed={res['seed']}."
    )
