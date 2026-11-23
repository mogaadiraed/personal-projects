"""
Unit Tests for CarbonSync Synthetic Data Generator & Messifier
==============================================================
"""

import json

import pandas as pd
import pytest

from carbonsync.synth.generator import get_project_root


@pytest.fixture(scope="module")
def project_root():
    return get_project_root()


def test_truth_data_integrity(project_root):
    """Verifies that ground truth parquet exists, has 2,922 days, and is noise-free."""
    truth_file = project_root / "data" / "truth" / "truth.parquet"
    assert truth_file.exists(), "truth.parquet must exist"

    df = pd.read_parquet(truth_file)
    assert len(df) == 2922, f"Expected 2922 days (8 years), got {len(df)}"

    expected_cols = [
        "date",
        "units_produced",
        "operating_hours",
        "shutdown_flag",
        "electricity_kwh",
        "fuel_oil_l",
        "trips",
        "diesel_l",
        "waste_kg",
        "water_m3",
        "co2e_scope1_t",
        "co2e_scope2_t",
        "co2e_other_t",
        "co2e_total_t",
    ]
    for col in expected_cols:
        assert col in df.columns, f"Missing expected column: {col}"

    # Ground truth must be complete (no missing values)
    assert df.isna().sum().sum() == 0, "Ground truth must not contain any NaNs"

    # Sanity checks on emissions
    assert (df["co2e_total_t"] > 0).all(), "Total emissions must always be strictly positive"
    assert (df["co2e_scope1_t"] >= 0).all()
    assert (df["co2e_scope2_t"] >= 0).all()
    assert (df["co2e_other_t"] >= 0).all()


def test_regime_shifts_present(project_root):
    """Verifies that solar contract and efficiency plan drops are observable in truth data."""
    df = pd.read_parquet(project_root / "data" / "truth" / "truth.parquet")
    df["date"] = pd.to_datetime(df["date"])

    # Solar contract from 2021-06-01
    pre_solar = df[(df["date"] >= "2020-06-01") & (df["date"] < "2021-06-01") & (df["shutdown_flag"] == 0)]
    post_solar = df[(df["date"] >= "2021-06-01") & (df["date"] < "2022-06-01") & (df["shutdown_flag"] == 0)]

    pre_elec_intensity = (pre_solar["electricity_kwh"] / pre_solar["units_produced"]).mean()
    post_elec_intensity = (post_solar["electricity_kwh"] / post_solar["units_produced"]).mean()

    # Solar reduces grid electricity intensity by ~15%
    drop_pct = (pre_elec_intensity - post_elec_intensity) / pre_elec_intensity
    assert 0.08 < drop_pct < 0.22, f"Solar drop intensity expected around 15%, got {drop_pct:.1%}"


def test_shutdown_patterns(project_root):
    """Verifies that August shutdowns and Christmas breaks have significantly reduced production."""
    df = pd.read_parquet(project_root / "data" / "truth" / "truth.parquet")
    df["date"] = pd.to_datetime(df["date"])

    aug_shutdowns = df[(df["date"].dt.month == 8) & (df["date"].dt.day <= 14)]
    assert aug_shutdowns["shutdown_flag"].all(), "All days in first half of August must be flagged as shutdown"

    normal_workdays = df[(df["shutdown_flag"] == 0) & (df["date"].dt.dayofweek < 5)]
    assert aug_shutdowns["units_produced"].mean() < 0.20 * normal_workdays["units_produced"].mean(), (
        "Shutdown production should drop below 20% of regular workday production"
    )


def test_inbox_and_messy_manifest(project_root):
    """Verifies that inbox has monthly CSV files and messy manifest documents defects."""
    inbox_dir = project_root / "data" / "inbox"
    csv_files = list(inbox_dir.glob("*.csv"))
    assert len(csv_files) >= 384, f"Expected at least 384 inbox CSVs, got {len(csv_files)}"

    manifest_file = project_root / "data" / "messy_manifest.json"
    assert manifest_file.exists(), "messy_manifest.json must exist"

    with open(manifest_file, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    assert len(manifest) >= 30, f"Expected at least 30 recorded defects, got {len(manifest)}"

    # Check for both unusable (quarantine) and recoverable defects
    unusable = [m for m in manifest if m.get("quarantine_expected")]
    recoverable = [m for m in manifest if not m.get("quarantine_expected")]

    assert len(unusable) > 0, "Must have at least one quarantine-expected file"
    assert len(recoverable) > 0, "Must have recoverable defect files"
