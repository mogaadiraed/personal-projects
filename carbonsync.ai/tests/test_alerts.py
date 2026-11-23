"""Alert checks (pure functions) and the saved alert state (first seen, history)."""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from carbonsync import alerts as al
from tests._synthetic import daily


def _forecast(df: pd.DataFrame, end: str, days: int = 90, width: float = 0.5):
    """One run trained until ``end`` forecasting the next ``days`` at the actual level, ± ``width`` t."""
    runs = pd.DataFrame([{"run_id": "r1", "end_date": pd.Timestamp(end), "created_at": pd.Timestamp(end)}])
    dates = pd.date_range(pd.Timestamp(end) + pd.Timedelta(days=1), periods=days, freq="D")
    level = float(df["co2e_total_t"].iloc[-1])
    fc = pd.DataFrame({"run_id": "r1", "date": dates, "yhat": level, "lo90": level - width, "hi90": level + width})
    return runs, fc


def test_days_outside_the_band_are_flagged_with_their_direction():
    df = daily("2025-01-01", "2025-06-30", august=1.0)
    runs, fc = _forecast(df, "2025-03-31")
    df.loc[df["date"] == "2025-05-10", "co2e_total_t"] += 5      # above
    df.loc[df["date"] == "2025-06-02", "co2e_total_t"] -= 5      # below
    m = al.matched(df, runs, fc, 90)
    assert len(m) == 89 and set(m["run_id"]) == {"r1"}       # window 2 Apr–30 Jun ∩ forecast 1 Apr–29 Jun
    an = al.anomalies(df, runs, fc, 90)
    assert list(an["date"].dt.strftime("%Y-%m-%d")) == ["2025-05-10", "2025-06-02"]
    assert list(an["direction"]) == ["above", "below"]
    [a] = al.check_anomalies(an, 90)
    assert a["kind"] == "anomaly" and a["severity"] == "warning" and a["data"]["above"] == 1
    assert "2 day(s)" in a["title_en"] and "," in a["detail_fr"]          # French decimals


def test_a_forecast_never_judges_the_days_it_was_trained_on():
    df = daily("2025-01-01", "2025-06-30", august=1.0)
    runs, fc = _forecast(df, "2025-06-30")                         # starts after the last actual day
    assert al.matched(df, runs, fc, 90).empty and al.check_anomalies(al.anomalies(df, runs, fc, 90), 90) == []


def test_departments_behind_and_stale_data():
    last = {"energy": pd.Timestamp("2026-03-31"), "production": pd.Timestamp("2026-03-31"),
            "logistics": pd.Timestamp("2026-02-28"), "waste_water": None}
    out = al.check_departments(last, date(2026, 4, 10), 45)
    assert [a["id"] for a in out] == ["dept:logistics"] and out[0]["data"]["lag_days"] == 31
    stale = al.check_departments(last, date(2026, 11, 21), 45)
    assert "data:stale" in {a["id"] for a in stale}
    assert al.check_departments({"energy": None}, date(2026, 1, 1), 45) == []


def test_year_end_alert_only_when_the_projection_passes_the_cap():
    proj = {"year": 2026, "total_t": 3222.0, "total_lo_t": 2464.0, "total_hi_t": 3882.0, "cap": 2748.8, "gap_t": 473.2}
    [a] = al.check_year(proj)
    assert a["severity"] == "critical" and "inside the 90% range" in a["detail_en"]
    assert al.check_year({**proj, "gap_t": -10}) == [] and al.check_year(None) == []


def test_router_drift_alert():
    res = [{"model_name": "chronos", "mase": 1.0}, {"model_name": "prophet", "mase": 0.8},
           {"model_name": "ens_pc", "mase": 0.9}]
    [a] = al.check_router(res, "chronos", 0.05)
    assert a["kind"] == "router" and a["data"]["best"] == "prophet"
    assert al.check_router(res, "prophet", 0.05) == []
    assert al.check_router([{"model_name": "chronos", "mase": 0.82}, {"model_name": "prophet", "mase": 0.8}],
                           "chronos", 0.05) == []                    # within the margin


def test_quarantine_and_settings():
    assert al.check_quarantine([]) == []
    assert al.check_quarantine(["a.csv", "b.csv"])[0]["title_en"].startswith("2 file")
    s = al.settings({"alerts": {"stale_after_days": 10}})
    assert s["stale_after_days"] == 10 and s["anomaly_window_days"] == 90


def test_saved_state_keeps_first_seen_and_moves_resolved_alerts(tmp_path, monkeypatch):
    def fake(found):
        def evaluate(root=None, cfg=None, today=None):
            return [dict(a) for a in found], {"year": 2026, "anomalies": pd.DataFrame(
                columns=["date", "actual", "yhat", "lo90", "hi90", "run_id", "direction"])}
        return evaluate

    a = al._alert("budget:2026-04", "budget", "critical", "fr", "en", "dfr", "den", "budget")
    b = al._alert("data:stale", "stale", "warning", "fr", "en", "dfr", "den", "intake")
    monkeypatch.setattr(al, "evaluate", fake([a, b]))
    first = al.check(tmp_path, {}, trigger="test")
    assert al.counts(first) == {"critical": 1, "warning": 1, "info": 0}
    seen = {x["id"]: x["first_seen"] for x in first["alerts"]}

    monkeypatch.setattr(al, "evaluate", fake([b]))
    second = al.check(tmp_path, {}, trigger="test")
    assert [x["id"] for x in second["alerts"]] == ["data:stale"]
    assert second["alerts"][0]["first_seen"] == seen["data:stale"]
    assert [h["id"] for h in second["history"]] == ["budget:2026-04"] and second["history"][0]["resolved_at"]
    assert al.read_state(tmp_path)["checked_at"] == second["checked_at"]
    assert al.read_state(tmp_path / "nowhere") == {}


@pytest.mark.parametrize("bad", ["{not json", ""])
def test_a_damaged_state_file_reads_as_empty(tmp_path, bad):
    p = al.state_path(tmp_path)
    p.parent.mkdir(parents=True)
    p.write_text(bad, encoding="utf-8")
    assert al.read_state(tmp_path) == {}
