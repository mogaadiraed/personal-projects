"""Small, fully known daily data for the target, inventory and alert tests (no database needed)."""
from __future__ import annotations

import numpy as np
import pandas as pd

FACTORS = {"electricity_kgco2_per_kwh": 0.483, "fuel_oil_kgco2_per_l": 2.68, "diesel_kgco2_per_l": 2.68,
           "waste_kgco2_per_kg": 0.5, "water_kgco2_per_m3": 0.3}


def daily(start: str = "2019-01-01", end: str = "2026-03-31", august: float = 0.6) -> pd.DataFrame:
    """One row per day; August runs at ``august`` × the normal activity (summer closure).

    Emissions are computed from the activity with ``FACTORS``, as the pipeline does, so totals are consistent.
    """
    dates = pd.date_range(start, end, freq="D")
    k = np.where(dates.month == 8, august, 1.0)
    d = pd.DataFrame({
        "date": dates,
        "electricity_kwh": 12_000.0 * k,
        "fuel_oil_l": 900.0 * k,
        "diesel_l": 250.0 * k,
        "waste_kg": 500.0 * k,
        "water_m3": 60.0 * k,
        "units_produced": 1_000.0 * k,
        "is_weekend": (dates.dayofweek >= 5).astype(int),
    })
    d["co2e_scope1_t"] = (d["fuel_oil_l"] * FACTORS["fuel_oil_kgco2_per_l"]
                          + d["diesel_l"] * FACTORS["diesel_kgco2_per_l"]) / 1000
    d["co2e_scope2_t"] = d["electricity_kwh"] * FACTORS["electricity_kgco2_per_kwh"] / 1000
    d["co2e_other_t"] = (d["waste_kg"] * FACTORS["waste_kgco2_per_kg"] + d["water_m3"] * FACTORS["water_kgco2_per_m3"]) / 1000
    d["co2e_total_t"] = d["co2e_scope1_t"] + d["co2e_scope2_t"] + d["co2e_other_t"]
    return d
