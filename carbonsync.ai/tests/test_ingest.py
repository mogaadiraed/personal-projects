"""
Unit Tests for CarbonSync Ingestion & Validation Layer
======================================================
"""

import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pandera.pandas as pa
import pytest

from carbonsync.ingest.load import get_duckdb_path
from carbonsync.ingest.schemas import EnergySchema
from carbonsync.ingest.validate import (
    COLUMN_ALIASES,
    detect_format,
)


@pytest.fixture(scope="module")
def project_root():
    return Path(__file__).resolve().parents[1]


def test_pandera_energy_schema_validation():
    """Checks that EnergySchema accepts valid ranges and catches range violations."""
    valid_df = pd.DataFrame(
        {
            "date": pd.date_range("2023-01-01", periods=5, freq="D"),
            "electricity_kwh": [10000.0, 12000.0, np.nan, 14000.0, 9500.0],
            "fuel_oil_l": [200.0, 250.0, 180.0, np.nan, 220.0],
        }
    )
    validated = EnergySchema.validate(valid_df)
    assert len(validated) == 5

    # Out of range test (negative electricity)
    invalid_df = valid_df.copy()
    invalid_df.loc[0, "electricity_kwh"] = -500.0
    with pytest.raises(pa.errors.SchemaError):
        EnergySchema.validate(invalid_df)


def test_format_detection():
    """Checks delimiter and decimal detection for French CSVs."""
    french_lines = [
        "date;electricity_kwh;fuel_oil_l",
        "2023-01-01;12450,5;210,0",
        "2023-01-02;11800,0;195,5",
    ]
    sep, dec = detect_format(french_lines)
    assert sep == ";"
    assert dec == ","

    standard_lines = [
        "date,electricity_kwh,fuel_oil_l",
        "2023-01-01,12450.5,210.0",
    ]
    sep_std, dec_std = detect_format(standard_lines)
    assert sep_std == ","
    assert dec_std == "."


def test_column_alias_mapping():
    """Checks French department column alias mappings."""
    assert COLUMN_ALIASES["electricité (kwh)"] == "electricity_kwh"
    assert COLUMN_ALIASES["fioul (l)"] == "fuel_oil_l"
    assert COLUMN_ALIASES["quantité produite"] == "units_produced"
    assert COLUMN_ALIASES["nombre de trajets"] == "trips"
    assert COLUMN_ALIASES["gazole (l)"] == "diesel_l"


def test_quarantine_reports_exist(project_root):
    """Verifies that quarantined files produce valid JSON error reports."""
    quarantine_dir = project_root / "data" / "quarantine"
    assert quarantine_dir.exists()

    error_reports = list(quarantine_dir.glob("*.errors.json"))
    assert len(error_reports) >= 8, f"Expected at least 8 error reports, found {len(error_reports)}"

    for rep_file in error_reports[:5]:
        with open(rep_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert "file_name" in data
        assert "department" in data
        assert "errors" in data
        assert len(data["errors"]) > 0
        assert "suggested_fix" in data
        assert len(data["suggested_fix"]) > 0


def test_duckdb_tables_populated(project_root):
    """Checks that DuckDB contains the raw tables with audit tracking."""
    db_path = get_duckdb_path()
    assert db_path.exists(), "carbonsync.duckdb must exist"

    conn = duckdb.connect(str(db_path), read_only=True)
    tables = [t[0] for t in conn.execute("SHOW TABLES").fetchall()]

    for expected in ["raw_energy", "raw_production", "raw_logistics", "raw_waste_water"]:
        assert expected in tables, f"Missing table {expected} in DuckDB"
        count = conn.execute(f"SELECT COUNT(*) FROM {expected}").fetchone()[0]
        assert count > 2000, f"Table {expected} should have > 2000 rows, found {count}"

        # Check audit columns
        cols = [c[1] for c in conn.execute(f"PRAGMA table_info('{expected}')").fetchall()]
        assert "source_file" in cols
        assert "loaded_at" in cols

    conn.close()
