"""
Unit Tests for CarbonSync Daily Feature Table & Emissions Calculation
=====================================================================
"""

from pathlib import Path

import duckdb
import pytest

from carbonsync.features.daily import load_config
from carbonsync.ingest.load import get_duckdb_path


@pytest.fixture(scope="module")
def project_root():
    return Path(__file__).resolve().parents[1]


def test_daily_table_completeness():
    """Checks that the daily table has at least 2,922 continuous days and calendar features."""
    db_path = get_duckdb_path()
    assert db_path.exists()

    conn = duckdb.connect(str(db_path), read_only=True)
    count, span = conn.execute("SELECT COUNT(*), date_diff('day', MIN(date), MAX(date)) + 1 FROM daily").fetchone()
    assert count >= 2922, f"Expected at least the 2922 original days, got {count}"
    assert count == span, f"Daily table must be continuous: {count} rows over {span} days"

    # Verify calendar columns
    cols = [c[1] for c in conn.execute("PRAGMA table_info('daily')").fetchall()]
    expected_cols = [
        "date",
        "electricity_kwh",
        "fuel_oil_l",
        "units_produced",
        "operating_hours",
        "shutdown_flag",
        "trips",
        "diesel_l",
        "waste_kg",
        "water_m3",
        "is_imputed",
        "day_of_week",
        "is_weekend",
        "month",
        "day_of_year",
        "is_planned_shutdown",
        "solar_regime",
        "efficiency_regime",
    ]
    for col in expected_cols:
        assert col in cols, f"Missing column '{col}' in daily table"

    conn.close()


def test_emissions_calculation_accuracy():
    """Checks that CO2e emissions math matches config emission factors."""
    cfg = load_config()
    ef_elec = float(cfg["emission_factors"]["electricity_kgco2_per_kwh"])
    ef_fuel = float(cfg["emission_factors"]["fuel_oil_kgco2_per_l"])
    ef_diesel = float(cfg["emission_factors"]["diesel_kgco2_per_l"])

    db_path = get_duckdb_path()
    conn = duckdb.connect(str(db_path), read_only=True)

    # Pick 5 complete rows
    sample = conn.execute(
        """
        SELECT electricity_kwh, fuel_oil_l, diesel_l, waste_kg, water_m3,
               co2e_scope1_t, co2e_scope2_t, co2e_other_t, co2e_total_t
        FROM emissions_daily
        WHERE co2e_total_t IS NOT NULL AND electricity_kwh IS NOT NULL
        LIMIT 5
        """
    ).fetchdf()

    assert len(sample) == 5

    for _, row in sample.iterrows():
        expected_scope2 = round((row["electricity_kwh"] * ef_elec) / 1000.0, 4)
        assert abs(row["co2e_scope2_t"] - expected_scope2) < 1e-3

        expected_scope1 = round((row["fuel_oil_l"] * ef_fuel + row["diesel_l"] * ef_diesel) / 1000.0, 4)
        assert abs(row["co2e_scope1_t"] - expected_scope1) < 1e-3

        expected_total = round(row["co2e_scope1_t"] + row["co2e_scope2_t"] + row["co2e_other_t"], 4)
        assert abs(row["co2e_total_t"] - expected_total) < 1e-3

    conn.close()


def test_imputation_flags_and_sanity():
    """Checks that is_imputed is binary and between 5% and 35%."""
    db_path = get_duckdb_path()
    conn = duckdb.connect(str(db_path), read_only=True)

    stats = conn.execute("SELECT SUM(is_imputed), COUNT(*) FROM daily").fetchone()
    imputed_count, total_count = stats[0], stats[1]
    pct = imputed_count / total_count

    assert 0.05 < pct < 0.35, f"Imputed percentage {pct:.1%} should be between 5% and 35%"
    conn.close()


def test_forecasting_and_backtest_tables_exist():
    """Verifies that DuckDB contains the tables needed for forecast and backtest engines."""
    db_path = get_duckdb_path()
    conn = duckdb.connect(str(db_path), read_only=True)
    tables = [t[0] for t in conn.execute("SHOW TABLES").fetchall()]

    for t in ["forecast_runs", "forecasts", "backtest_results"]:
        assert t in tables, f"Missing required table: {t}"

    conn.close()
