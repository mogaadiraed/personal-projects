"""Market-based Scope 2, Scope 3 screening and the breakdown by plant zone."""
from __future__ import annotations

import copy

import pandas as pd
import pytest
import yaml

from app.data.loader import get_project_root
from carbonsync.inventory import breakdown as bd
from carbonsync.inventory import scope2, scope3
from tests._synthetic import FACTORS, daily

BASE = {"emission_factors": FACTORS}
YEAR = ("2025-01-01", "2025-12-31")


@pytest.fixture(scope="module")
def df():
    return daily("2024-01-01", "2026-03-31")


# ---------------------------------------------------------------------------
# Scope 2
# ---------------------------------------------------------------------------
def test_without_instrument_market_equals_location(df):
    t = scope2.totals(df, BASE, *YEAR)
    kwh = float(df[df["date"].dt.year == 2025]["electricity_kwh"].sum())
    assert t["location_t"] == pytest.approx(kwh * 0.483 / 1000)
    assert t["market_t"] == pytest.approx(t["location_t"])
    assert t["covered_pct"] == 0 and t["residual_mix_is_grid_proxy"]
    assert "même chiffre" in scope2.note(t, "fr") or "same figure" in scope2.note(t, "en")


def test_share_instrument_from_a_date(df):
    cfg = {**BASE, "scope2": {"residual_mix_kgco2_per_kwh": 0.5, "instruments": [
        {"name": "Green tariff", "kind": "green_tariff", "start": "2025-07-01", "share_of_grid_kwh": 0.3,
         "kgco2_per_kwh": 0.0}]}}
    d = scope2.daily(df, cfg)
    y = d[d["date"].dt.year == 2025]
    first, second = y[y["date"] < "2025-07-01"], y[y["date"] >= "2025-07-01"]
    assert second["covered_kwh"].sum() == pytest.approx(second["electricity_kwh"].sum() * 0.3)
    assert first["covered_kwh"].sum() == 0
    t = scope2.totals(df, cfg, *YEAR)
    expected = (first["electricity_kwh"].sum() * 0.5 + second["electricity_kwh"].sum() * 0.7 * 0.5) / 1000
    assert t["market_t"] == pytest.approx(expected)
    assert 0 < t["covered_pct"] < 30 and not t["residual_mix_is_grid_proxy"]


def test_volume_instrument_never_covers_more_than_the_kwh_bought(df):
    cfg = {**BASE, "scope2": {"instruments": [{"name": "Huge PPA", "kind": "ppa", "mwh_per_year": 1e9}]}}
    t = scope2.totals(df, cfg, *YEAR)
    assert t["covered_pct"] == pytest.approx(100) and t["market_t"] == pytest.approx(0)


def test_disabled_or_expired_instruments_are_ignored(df):
    cfg = {**BASE, "scope2": {"instruments": [
        {"name": "off", "share_of_grid_kwh": 0.5, "enabled": False},
        {"name": "old", "share_of_grid_kwh": 0.5, "end": "2020-12-31"}]}}
    t = scope2.totals(df, cfg, *YEAR)
    assert t["covered_kwh"] == 0 and t["market_t"] == pytest.approx(t["location_t"])


# ---------------------------------------------------------------------------
# Scope 3
# ---------------------------------------------------------------------------
def test_scope3_covers_the_15_categories_with_a_status(df):
    rows = scope3.screening(df, BASE, *YEAR)
    assert set(rows["category"]) == set(range(1, 16))
    assert set(rows["status"]) <= {"calculated", "estimated", "excluded"}
    excl = rows[rows["status"] == "excluded"]
    assert not excl.empty and excl["t"].isna().all()
    assert (excl["method_en"].str.len() > 5).all() and (excl["method_fr"].str.len() > 5).all()   # the reason
    inc = rows[rows["status"] != "excluded"]
    assert (inc["t"] >= 0).all()


def test_purchased_copper_and_pvc_follow_the_units_produced(df):
    s = scope3.settings(BASE)
    rows = scope3.screening(df, BASE, *YEAR)
    units = float(df[df["date"].dt.year == 2025]["units_produced"].sum())
    names = {m["name_en"].lower(): m for m in s["purchased_materials"]}
    assert any("copper" in n for n in names) and any("pvc" in n for n in names)
    for m in s["purchased_materials"]:
        row = rows[rows["item_en"] == m["name_en"]].iloc[0]
        assert row["status"] == "estimated" and row["category"] == 1
        assert row["t"] == pytest.approx(units * m["kg_per_unit"] * m["kgco2_per_kg"] / 1000, rel=1e-3)
    commuting = rows[rows["category"] == 7]
    assert not commuting.empty and (commuting["status"] == "estimated").all()


def test_scope3_summary_adds_up(df):
    s = scope3.summary(df, BASE, *YEAR)
    assert s["total_t"] == pytest.approx(s["calculated_t"] + s["estimated_t"])
    assert s["total_t"] == pytest.approx(sum(c["t"] for c in s["by_category"]))
    assert 2 in s["excluded_categories"] and 1 not in s["excluded_categories"]


def test_scope3_assumptions_can_be_changed():
    cfg = copy.deepcopy(BASE)
    cfg["scope3"] = {"commuting": {"employees": 0}}
    rows = scope3.screening(daily("2025-01-01", "2025-12-31"), cfg)
    assert rows[rows["category"] == 7]["t"].sum() == 0


# ---------------------------------------------------------------------------
# Breakdown by zone
# ---------------------------------------------------------------------------
def test_default_keys_are_valid_and_zones_add_up_to_the_site_total(df):
    s = bd.settings(BASE)
    assert {"P1", "P2", "P3"} <= set(s["zones"]) and bd.validate(s) == []
    zones = bd.by_zone(df, BASE, *YEAR)
    site = float(df[df["date"].dt.year == 2025]["co2e_total_t"].sum())
    assert zones["t"].sum() == pytest.approx(site)
    assert zones["share_pct"].sum() == pytest.approx(100)
    items = bd.by_item(df, BASE, *YEAR)
    assert items["t"].sum() == pytest.approx(site)


def test_monthly_allocation_and_project_config():
    cfg = yaml.safe_load((get_project_root() / "config.yaml").read_text(encoding="utf-8"))
    assert bd.validate(bd.settings(cfg)) == []                   # the shipped keys add up
    m = bd.allocate(daily("2025-01-01", "2025-03-31"), cfg, freq="M")
    assert sorted(m["period"].unique()) == ["2025-01-01", "2025-02-01", "2025-03-01"]


def test_validate_reports_bad_keys():
    s = bd.settings(BASE)
    s["keys"]["electricity_kwh"] = [{"fr": "x", "en": "x", "zone": "P1", "share": 0.5},
                                    {"fr": "y", "en": "y", "zone": "NOPE", "share": 0.2}]
    errors = bd.validate(s)
    assert any("not 1" in e for e in errors) and any("NOPE" in e for e in errors)


def test_empty_period_gives_an_empty_frame(df):
    assert bd.allocate(df, BASE, "2030-01-01", "2030-12-31").empty
    assert isinstance(bd.by_zone(df, BASE, "2030-01-01", "2030-12-31"), pd.DataFrame)
