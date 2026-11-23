"""Assistant foundations: Analyse maths, read-only tools, guide search, privacy.

Synthetic-data tests check the maths against hand-computed answers; the other tests run
read-only against the project database and check that every tool agrees with the
dashboard's own figures (build_insights), whatever data has been uploaded.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from carbonsync.assistant import analyst, knowledge, tools
from carbonsync.assistant.context import DataContext, load_context
from carbonsync.export.insights import Fmt, build_insights

ROOT = Path(__file__).resolve().parents[1]
EF = {"electricity_kgco2_per_kwh": 0.5, "fuel_oil_kgco2_per_l": 2.5, "diesel_kgco2_per_l": 2.5,
      "waste_kgco2_per_kg": 0.5, "water_kgco2_per_m3": 0.25}


# ---------------------------------------------------------------------------
# Synthetic context with known answers
# ---------------------------------------------------------------------------
def _synthetic(last: str = "2024-03-31", fc_days: int = 91) -> DataContext:
    """Two flat years: Q1 2024 has +10 % production and −10 % electricity per unit vs Q1 2023."""
    dates = pd.date_range("2022-01-01", last, freq="D")
    df = pd.DataFrame({"date": dates})
    y24 = df["date"] >= "2024-01-01"
    df["units_produced"] = np.where(y24, 1100.0, 1000.0)
    df["electricity_kwh"] = df["units_produced"] * np.where(y24, 9.0, 10.0)
    df["fuel_oil_l"] = df["units_produced"] * 0.4
    df["trips"] = 20.0
    df["diesel_l"] = 300.0
    df["waste_kg"] = 500.0
    df["water_m3"] = 40.0
    df["is_imputed"] = 0
    df["co2e_scope2_t"] = df["electricity_kwh"] * EF["electricity_kgco2_per_kwh"] / 1000
    df["co2e_scope1_t"] = (df["fuel_oil_l"] + df["diesel_l"]) * 2.5 / 1000
    df["co2e_other_t"] = (df["waste_kg"] * 0.5 + df["water_m3"] * 0.25) / 1000
    df["co2e_total_t"] = df["co2e_scope1_t"] + df["co2e_scope2_t"] + df["co2e_other_t"]
    start = pd.Timestamp(last) + pd.Timedelta(days=1)
    fdates = pd.date_range(start, periods=fc_days, freq="D")
    fc = pd.DataFrame({"date": fdates, "horizon": range(1, fc_days + 1), "yhat": 10.0, "lo90": 8.0, "hi90": 12.0})
    run = pd.Series({"run_id": "test_run", "model_name": "ens_pc", "history_days": len(df), "horizon_days": fc_days,
                     "start_date": dates[0], "end_date": dates[-1], "created_at": pd.Timestamp("2024-04-01")})
    cfg = {"emission_factors": EF, "budget": {"annual_cap_tco2e": 3000, "monthly_weights": {m: 1 / 12 for m in range(1, 13)}}}
    return DataContext(root=ROOT, cfg=cfg, config_path=None, df=df, runs=pd.DataFrame([run]), run=run, fc=fc)


def test_hotspots_window_and_change():
    ctx = _synthetic()
    hs = analyst.hotspots(ctx, 3)
    assert hs["start"] == pd.Timestamp("2024-01-01") and hs["end"] == pd.Timestamp("2024-03-31")
    assert hs["previous_start"] == pd.Timestamp("2023-01-01") and hs["previous_available"]
    s2 = next(r for r in hs["rows"] if r["key"] == "s2")
    # 1100 × 9 = 9900 kWh vs 10000 kWh per day → −1 %
    assert s2["change_pct"] == pytest.approx((9900 * 91 / (10000 * 90) - 1) * 100)  # 2024 is a leap year
    assert s2["per_day_recent"] == pytest.approx(9900 * 0.5 / 1000)


def test_lmdi_is_exact_and_splits_volume_and_intensity():
    ctx = _synthetic()
    dec = analyst.decompose(ctx, analyst.hotspots(ctx, 3))
    assert dec["volume_t_per_day"] + dec["intensity_t_per_day"] == pytest.approx(dec["change_t_per_day"], abs=1e-12)
    el = next(s for s in dec["by_source"] if s["key"] == "electricity")
    assert el["change_t_per_day"] == pytest.approx((9900 - 10000) * 0.5 / 1000)
    w = (4.95 - 5.0) / math.log(4.95 / 5.0)
    assert el["volume_t_per_day"] == pytest.approx(w * math.log(1.1))      # +10 % production
    assert el["intensity_t_per_day"] == pytest.approx(w * math.log(0.9))   # −10 % kWh per unit
    diesel = next(s for s in dec["by_source"] if s["key"] == "diesel")
    assert diesel["change_t_per_day"] == pytest.approx(0.0, abs=1e-12)    # flat trips, flat litres


def test_projection_adds_actual_forecast_and_estimate():
    ctx = _synthetic()
    hs = analyst.hotspots(ctx, 3)
    p = analyst.project_year(ctx, hs)
    assert p["year"] == 2024 and p["remaining_days"] == 275
    assert p["actual_ytd"] == pytest.approx(ctx.df[ctx.df["date"].dt.year == 2024]["co2e_total_t"].sum())
    assert p["forecast_t"] == pytest.approx(91 * 10.0)
    # beyond the forecast: same day last year × trend ratio (clipped)
    ly = ctx.df[(ctx.df["date"] >= "2023-07-01") & (ctx.df["date"] <= "2023-12-31")]["co2e_total_t"].sum()
    assert p["estimate_t"] == pytest.approx(ly * hs["trend_ratio"])
    assert p["total_t"] == pytest.approx(p["actual_ytd"] + p["forecast_t"] + p["estimate_t"])
    assert p["gap_t"] == pytest.approx(p["total_t"] - 3000)
    assert p["total_lo_t"] < p["total_t"] < p["total_hi_t"]
    assert sum(m["projected"] for m in p["months"]) == pytest.approx(p["total_t"])


def test_reductions_convert_tonnes_to_kwh_and_percent():
    ctx = _synthetic()
    hs = analyst.hotspots(ctx, 3)
    p = analyst.project_year(ctx, hs, cap=2500)       # force a gap
    red = analyst.reductions(ctx, hs, p, 3)
    assert red["needed_t"] == pytest.approx(p["gap_t"]) and red["needed_t"] > 0
    for w in red["windows"]:
        assert w["t_per_day"] * w["days"] == pytest.approx(p["gap_t"])
        el = w["electricity_only"]
        assert el["per_day"] == pytest.approx(w["t_per_day"] * 1000 / 0.5)
        assert el["pct"] == pytest.approx(el["per_day"] / 9900 * 100)
        assert el["feasibility"] == analyst.feasibility(el["pct"])
    assert red["uniform_pct"] == pytest.approx(p["gap_t"] / p["remaining_t"] * 100)
    assert sum(m["t"] for m in red["mix"]) == pytest.approx(p["gap_t"])
    assert analyst.reductions(ctx, hs, analyst.project_year(ctx, hs, cap=10_000), 3) is None  # no gap, no target


def test_feasibility_labels():
    assert analyst.feasibility(4) == "realistic"
    assert analyst.feasibility(10) == "realistic"
    assert analyst.feasibility(15) == "ambitious"
    assert analyst.feasibility(35) == "unrealistic"
    assert analyst.feasibility(float("nan")) == "unknown"


def test_lever_minimum_closes_the_gap_exactly():
    from carbonsync.scenarios.whatif import simulate_scenario

    ctx = _synthetic()
    hs = analyst.hotspots(ctx, 3)
    p = analyst.project_year(ctx, hs, cap=2500)
    assert p["gap_t"] > 0
    lev = analyst.levers(ctx, p)
    eff = lev["minimum"]["efficiency_pct"]
    res = simulate_scenario(p["baseline"][["date", "yhat", "lo90", "hi90"]], efficiency_gain_pct=eff,
                            scope_shares=lev["shares"])
    assert -res["summary"]["net_delta_tco2e"] == pytest.approx(p["gap_t"], rel=1e-3)


def test_text_is_bilingual_and_clean():
    ctx = _synthetic()
    for lang in ("fr", "en"):
        res = {"lang": lang, "months": 3, "forecast_name": "Ens-PC", "alerts": []}
        hs = analyst.hotspots(ctx, 3)
        res.update(hotspots=hs, drivers=analyst.drivers(ctx, hs), decomposition=analyst.decompose(ctx, hs))
        res["projection"] = analyst.project_year(ctx, hs, cap=2500)
        res["reductions"] = analyst.reductions(ctx, hs, res["projection"], 3)
        res["levers"] = analyst.levers(ctx, res["projection"])
        findings, plan = analyst._write(Fmt(lang), res)
        text = " ".join(findings + plan)
        assert len(findings) >= 6 and len(plan) >= 3
        assert "nan" not in text.lower().split() and "None" not in text
        assert ("Projection" in text) if lang == "fr" else ("projection" in text)
        assert " -1" not in text and "(-" not in text, "negative numbers use the typographic minus"


def test_period_label():
    f = Fmt("fr")
    assert analyst.period_label(f, pd.Timestamp("2026-01-01"), pd.Timestamp("2026-03-31")) == "janv.–mars 2026"
    assert "2025" in analyst.period_label(Fmt("en"), pd.Timestamp("2025-10-01"), pd.Timestamp("2026-03-31"))


# ---------------------------------------------------------------------------
# Tools against the project database (read-only)
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def ctx():
    return load_context()


@pytest.fixture(scope="module")
def ins():
    return build_insights("fr")


def test_tools_never_write(ctx):
    db = ctx.root / ctx.cfg.get("paths", {}).get("duckdb", "data/carbonsync.duckdb")
    before = db.stat().st_mtime_ns
    for name in tools.TOOLS:
        args = {"question": "budget"} if name == "explain_dashboard" else {}
        if name == "compare_periods":
            args = {"period_start": str((ctx.last - pd.Timedelta(days=89)).date()), "period_end": str(ctx.last.date())}
        out = tools.execute(name, args)
        assert out["ok"], (name, out.get("error"))
        json.dumps(out)  # plain JSON
    assert db.stat().st_mtime_ns == before


def test_overview_matches_dashboard(ctx, ins):
    r = tools.execute("get_overview", {})["result"]
    assert r["period"]["days"] == ins["period"]["days"] == len(ctx.df)
    assert r["last_12_months"]["total_t"] == pytest.approx(ins["totals"]["last12"], rel=1e-3)
    assert r["budget"]["cap"] == pytest.approx(ins["budget"]["cap"], rel=1e-3)
    assert r["conclusions"] == ins["conclusions"]
    assert r["_sources"]["data_until"] == str(ctx.last.date())


def test_query_emissions_sums_match(ctx, ins):
    r = tools.execute("query_emissions", {"start": str(ctx.first.date()), "granularity": "year"})["result"]
    cols = r["table"]["columns"]
    by_year = {row[0]: row[cols.index("total_t")] for row in r["table"]["rows"]}
    for _, a in ins["annual"].iterrows():
        assert by_year[str(int(a["year"]))] == pytest.approx(a["total"], rel=1e-3)
    assert r["totals"]["total_t"] == pytest.approx(ctx.df["co2e_total_t"].sum(), abs=0.5)  # ≥1000 rounded to units
    daily = tools.execute("query_emissions", {"start": str(ctx.first.date()), "granularity": "day"})["result"]
    assert len(daily["table"]["rows"]) <= tools.MAX_ROWS and daily["notes"]


def test_compare_periods_matches_analyse(ctx):
    hs = analyst.hotspots(ctx, 3)
    r = tools.execute("compare_periods", {"period_start": str(hs["start"].date()), "period_end": str(hs["end"].date())})
    total = next(m for m in r["result"]["metrics"] if m["metric"] == "total_t")
    row = next(x for x in hs["rows"] if x["key"] == "total")
    assert total["period_total"] == pytest.approx(row["recent_t"], rel=1e-3)
    dec = r["result"]["decomposition"]
    assert dec["volume_t_per_day"] + dec["intensity_t_per_day"] == pytest.approx(dec["change_t_per_day"], abs=2e-3)


def test_analyse_on_real_data_is_consistent(ctx):
    res = analyst.analyse("fr", months=3, ctx=ctx)
    p = res["projection"]
    assert p["total_t"] == pytest.approx(p["actual_ytd"] + p["forecast_t"] + p["estimate_t"])
    assert p["actual_until"] == ctx.last
    if p["gap_t"] > 0:
        red = res["reductions"]
        ef = ctx.factors["electricity_kgco2_per_kwh"]
        for w in red["windows"]:
            assert w["electricity_only"]["per_day"] == pytest.approx(w["t_per_day"] * 1000 / ef)
    else:
        assert res["reductions"] is None
    assert res["findings"] and res["plan"]
    en = tools.execute("analyse", {}, tools.ToolContext("en"))["result"]
    assert en["findings"] and en["action_plan"] and "nan" not in json.dumps(en["findings"]).lower()


def test_scenario_tool_matches_simulator(ctx):
    r = tools.execute("run_scenario", {"solar_pct": 10, "efficiency_pct": 5})["result"]
    lev = analyst.levers(ctx, analyst.project_year(ctx, analyst.hotspots(ctx, 3)))
    combo = next(o for o in lev["options"] if o["key"] == "combo")
    assert r["year_effect"]["saved_rest_of_year_t"] == pytest.approx(combo["saved_t"], rel=1e-3)
    assert r["forecast_horizon"]["horizon_days"] == len(ctx.fc)


def test_budget_tool(ins):
    r = tools.execute("get_budget", {})["result"]
    assert len(r["months"]) == 12 and r["cap_t"] == pytest.approx(ins["budget"]["cap"], rel=1e-3)
    fy = r["full_year_estimate"]
    assert fy["gap_t"] == pytest.approx(fy["total_t"] - r["cap_t"], abs=0.6)  # total_t is rounded to whole tonnes
    past = tools.execute("get_budget", {"year": ins["budget"]["year"] - 1})["result"]
    assert past["total_t"] == pytest.approx(sum(m["actual"] for m in past["months"]), abs=0.5)
    assert not tools.execute("get_budget", {"year": 2099})["ok"]


def test_privacy_aggregated_mode():
    agg = tools.ToolContext("en", privacy="aggregated")
    for name, args in (("query_emissions", {"granularity": "day"}), ("get_forecast", {"granularity": "day"})):
        r = tools.execute(name, args, agg)["result"]
        assert r["granularity"] == "month" and any("monthly" in n for n in r["notes"])
    for name in tools.TOOLS:
        args = {"question": "upload"} if name == "explain_dashboard" else {}
        if name == "compare_periods":
            continue
        blob = json.dumps(tools.execute(name, args, agg), ensure_ascii=False)
        assert ":\\\\" not in blob and str(Path.home()) not in blob, name


def test_validation_errors():
    bad = [("query_emissions", {"granularity": "hour"}), ("query_emissions", {"oops": 1}),
           ("run_scenario", {"solar_pct": 99}), ("compare_periods", {"period_start": "2025-01-01"}),
           ("query_emissions", {"start": "not a date"}), ("get_forecast", {"run_id": "nope"}), ("no_such_tool", {})]
    for name, args in bad:
        out = tools.execute(name, args)
        assert out["ok"] is False and out["error"], (name, args)
    ok = tools.execute("run_scenario", {"solar_pct": "10", "efficiency_pct": 5.0})
    assert ok["ok"] and ok["args"]["solar_pct"] == 10.0
    assert tools.execute("query_emissions", {"metrics": "total_t,scope2_t"})["ok"]


def test_jsonable_and_scrub():
    out = tools.jsonable({"a": float("nan"), "b": np.int64(3), "c": pd.Timestamp("2026-03-31"),
                          "d": pd.Timestamp("2026-03-31 10:15:00"), "e": np.float32(1234.5678), "f": [np.nan, 0.123456],
                          "g": r"C:\Users\someone\data\file.csv was rejected", "h": Path("/home/someone/x.csv")})
    assert out == {"a": None, "b": 3, "c": "2026-03-31", "d": "2026-03-31T10:15:00", "e": 1235, "f": [None, 0.123],
                   "g": "file.csv was rejected", "h": "x.csv"}


def test_specs_are_valid_schemas():
    specs = tools.specs()
    assert len(specs) == 16 and len({s["name"] for s in specs}) == 16
    for s in specs:
        p = s["parameters"]
        assert p["type"] == "object" and p["additionalProperties"] is False
        assert set(p.get("required", [])) <= set(p["properties"])
        assert len(s["description"]) > 40
        assert tools.status_label(s["name"], "fr") != tools.status_label(s["name"], "en")


# ---------------------------------------------------------------------------
# Guide search
# ---------------------------------------------------------------------------
def test_guides_have_matching_sections():
    fr, en = knowledge.sections("fr"), knowledge.sections("en")
    assert len(fr) == len(en) >= 15
    assert all(s.keywords and s.body for s in (*fr, *en))


@pytest.mark.parametrize(("question", "lang", "expected"), [
    ("comment ajouter 3 mois de données", "fr", "Dépôt de données"),
    ("télécharger le rapport pdf", "fr", "Rapports"),
    ("facteur d'émission de l'électricité", "fr", "Scopes et facteurs d'émission"),
    ("Jours imputés", "fr", "Imputation et quarantaine"),
    ("what does the model lab do", "en", "Model lab"),
    ("how do I see the full history", "en", "Overview"),
])
def test_guide_search(question, lang, expected):
    hits = knowledge.search(question, lang)
    assert hits and hits[0]["title"] == expected


def test_guide_search_no_match():
    assert knowledge.search("xyzzy plugh", "en") == []
    assert knowledge.fold("Électricité Œuvre") == "electricite œuvre"


def test_cli_runs(capsys):
    from carbonsync.assistant.__main__ import main

    assert main(["--tool", "get_methodology"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and out["result"]["emission_factors"]
    assert main(["--lang", "en", "--months", "2"]) == 0
    assert "CARBON ANALYSIS" in capsys.readouterr().out


def test_query_emissions_gives_changes_between_complete_periods():
    r = tools.execute("query_emissions", {"start": "2024-01-01", "end": "2025-12-31", "granularity": "year"})["result"]
    (row24, row25), cols = r["table"]["rows"], r["table"]["columns"]
    ch = r["changes_vs_previous"][0]
    tot = cols.index("total_t")
    assert (ch["period"], ch["vs"]) == ("2025", "2024")
    assert ch["total_t_change"] == pytest.approx(row25[tot] - row24[tot], abs=1)
    assert ch["total_t_change_pct"] == pytest.approx((row25[tot] / row24[tot] - 1) * 100, abs=0.05)


def test_year_projection_does_not_depend_on_the_period_studied(ctx):
    totals = {m: analyst.analyse("en", months=m, ctx=ctx)["projection"]["total_t"] for m in (1, 6, 12)}
    budget = tools.execute("get_budget", {})["result"]["full_year_estimate"]["total_t"]
    assert max(totals.values()) - min(totals.values()) < 1e-6
    assert budget == pytest.approx(totals[1], abs=0.5)
