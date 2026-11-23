"""Reduction target, yearly path, caps read on the path and seasonal monthly weights."""
from __future__ import annotations

import itertools

import pandas as pd
import pytest

from carbonsync import budget, targets
from tests._synthetic import daily

CFG = {"targets": {"base_year": 2019, "target_year": 2030, "reduction_pct": 30},
       "budget": {"cap_source": "path", "weights_source": "seasonal", "weights_years": 3, "annual_cap_tco2e": 3000}}


@pytest.fixture(scope="module")
def df():
    return daily()


def test_path_runs_from_the_base_year_to_the_target(df):
    p = targets.plan(CFG, df)
    base = float(df[df["date"].dt.year == 2019]["co2e_total_t"].sum())
    assert p.uses_path and p.base_emissions == pytest.approx(base)
    assert p.target_emissions == pytest.approx(base * 0.7)
    assert p.cap(2019) == pytest.approx(base)
    assert p.cap(2030) == pytest.approx(base * 0.7)
    assert p.cap(2040) == pytest.approx(base * 0.7)          # stays at the target after the target year
    assert p.cap(2026) == pytest.approx(base * (1 - 0.30 * 7 / 11))
    caps = [p.cap(y) for y in range(2019, 2031)]
    assert all(a > b for a, b in itertools.pairwise(caps))     # strictly decreasing every year
    assert p.warnings == []


def test_seasonal_weights_follow_the_site_profile(df):
    p = targets.plan(CFG, df)
    assert sum(p.weights.values()) == pytest.approx(1.0)
    assert p.weights_years == [2023, 2024, 2025]               # last 3 complete years (2026 is partial)
    assert p.weight(8) < p.weight(7) * 0.7                     # August runs at 60 %
    assert p.weight(1) == pytest.approx(p.weight(3))            # two 31-day months, same activity
    assert p.month_cap(2026, 8) == pytest.approx(p.cap(2026) * p.weight(8))


def test_path_table_flags_years_on_and_off_track(df):
    p = targets.plan(CFG, df)
    t = p.path_table(until=2030).set_index("year")
    assert list(t.index) == list(range(2019, 2031))
    assert t.loc[2019, "on_track"]                              # actual == cap
    assert not t.loc[2025, "on_track"]                          # flat emissions, falling cap
    assert t.loc[2025, "gap"] == pytest.approx(t.loc[2025, "actual"] - t.loc[2025, "cap"])
    assert pd.isna(t.loc[2026, "actual"])                       # incomplete year: no actual


def test_incomplete_base_year_falls_back_to_the_fixed_cap(df):
    cfg = {**CFG, "targets": {**CFG["targets"], "base_year": 2018}}
    p = targets.plan(cfg, df)
    assert not p.uses_path and p.cap(2026) == 3000
    assert any("2018" in w for w in p.warnings)


def test_legacy_budget_section_keeps_its_fixed_cap_and_weights(df):
    weights = {m: (0.1 if m == 1 else 0.9 / 11) for m in range(1, 13)}
    p = targets.plan({"budget": {"annual_cap_tco2e": 2500, "monthly_weights": weights}}, df)
    assert p.cap_source == "manual" and p.weights_source == "manual"
    assert p.cap(2026) == 2500 and p.weight(1) == pytest.approx(0.1)


def test_manual_weights_that_do_not_total_one_are_reported(df):
    cfg = {**CFG, "budget": {**CFG["budget"], "weights_source": "manual", "monthly_weights": {m: 0.1 for m in range(1, 13)}}}
    assert any("not 1" in w for w in targets.plan(cfg, df).warnings)


def test_settings_are_cleaned():
    t = targets.target_settings({"targets": {"base_year": 2020, "target_year": 2015, "reduction_pct": 140}})
    assert t["target_year"] == 2021 and t["reduction_pct"] == 100
    assert targets.budget_settings({})["cap_source"] == "path"
    assert targets.budget_settings({"budget": {"cap_source": "nonsense", "annual_cap_tco2e": 1}})["cap_source"] == "path"


def test_describe_and_as_dict(df):
    p = targets.plan(CFG, df)
    assert "−30 %" in p.describe("fr") and "2030" in p.describe("fr")
    assert "−30%" in p.describe("en")
    d = p.as_dict(2026)
    assert d["year"] == 2026 and d["annual_cap_t"] == pytest.approx(round(p.cap(2026), 1))
    assert set(d["monthly_weights"]) == set(range(1, 13))


def test_budget_month_uses_the_plan(df):
    p = targets.plan(CFG, df)
    mb = budget.evaluate_month(df, pd.DataFrame(), CFG, 2026, 3)
    assert mb.ceiling == pytest.approx(p.month_cap(2026, 3))
    assert budget.annual_cap(CFG, df, 2026) == pytest.approx(p.cap(2026))
    assert budget.month_weight(CFG, 8, df) == pytest.approx(p.weight(8))
    # with no data the cap is the fixed one and the weights are equal
    assert budget.annual_cap(CFG) == 3000
