"""Data intake, ingest parsing fixes and the model lab (no project data is modified)."""
from __future__ import annotations

import io
import zipfile

import numpy as np
import pandas as pd
import pytest

from carbonsync.ingest import intake
from carbonsync.ingest.load import load_config
from carbonsync.ingest.validate import parse_dates, validate_file
from carbonsync.models.lab import _score, run_experiment, seasonal_naive

FRENCH_ENERGY = (
    "# Export service Énergie – avril 2026\n"
    "date;Electricité (kWh);Fioul (L)\n"
    "01/04/2026;14077,36;542,75\n"
    "02/04/2026;13473,15;493,73\n"
    "03/04/2026;12001,50;401,10\n"
)


# ---------------------------------------------------------------------------
# Parsing fixes
# ---------------------------------------------------------------------------
def test_parse_dates_iso_is_never_day_first():
    out = parse_dates(pd.Series(["2025-03-05", "05/03/2025", "2025-12-31", "31/12/2025"]))
    assert [d.strftime("%Y-%m-%d") for d in out] == ["2025-03-05", "2025-03-05", "2025-12-31", "2025-12-31"]


def test_parse_dates_raises_on_garbage():
    with pytest.raises(ValueError):
        parse_dates(pd.Series(["2025-01-01", "not a date"]))


def test_french_decimal_commas_are_kept(tmp_path):
    cfg = load_config()
    cfg["paths"] = {**cfg.get("paths", {}), "processed": str(tmp_path / "processed"),
                    "quarantine": str(tmp_path / "quarantine")}
    src = tmp_path / "energie_2026-04.csv"
    src.write_text(FRENCH_ENERGY, encoding="utf-8")
    res = validate_file(src, config=cfg)
    assert res.success, res.errors
    df = pd.read_parquet(res.processed_path)
    assert df["date"].min() == pd.Timestamp("2026-04-01")
    assert df["electricity_kwh"].iloc[0] == pytest.approx(14077.36)
    assert df["fuel_oil_l"].iloc[1] == pytest.approx(493.73)


# ---------------------------------------------------------------------------
# Intake detection
# ---------------------------------------------------------------------------
def test_inspect_detects_department_from_name_and_profiles_dates():
    info = intake.inspect_upload("energie_2026-04.csv", FRENCH_ENERGY.encode())
    assert info.ok and info.department == "energie" and info.detected_from == "name"
    assert (info.rows, info.first_date, info.last_date) == (3, "2026-04-01", "2026-04-03")


def test_inspect_detects_department_from_columns_and_prefixes_name():
    content = b"date,trips,diesel_l\n2026-05-01,40,310.5\n"
    info = intake.inspect_upload("export mai.csv", content)
    assert info.department == "logistique" and info.detected_from == "columns"
    assert info.target_name == "logistique_export_mai.csv"


def test_inspect_drop_zone_department_wins_but_warns_on_mismatch():
    content = b"date,trips,diesel_l\n2026-05-01,40,310.5\n"
    info = intake.inspect_upload("mai.csv", content, department="energie")
    assert info.department == "energie" and info.target_name.startswith("energie_")
    assert any("logistique" in n for n in info.notes)


def test_inspect_rejects_unknown_files():
    assert not intake.inspect_upload("notes.txt", b"hello").ok
    assert not intake.inspect_upload("mystery.csv", b"a,b\n1,2\n").ok


def test_expand_archive_returns_only_csv():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("q1/energie_2026-01.csv", "date,electricity_kwh,fuel_oil_l\n2026-01-01,1,2\n")
        zf.writestr("q1/readme.txt", "x")
        zf.writestr("__MACOSX/._energie_2026-01.csv", "junk")
    files = intake.expand_archive("q1.zip", buf.getvalue())
    assert [n for n, _ in files] == ["energie_2026-01.csv"]


# ---------------------------------------------------------------------------
# Model lab
# ---------------------------------------------------------------------------
def _series(days: int = 500) -> pd.DataFrame:
    dates = pd.date_range("2023-01-01", periods=days, freq="D")
    weekly = np.where(dates.dayofweek >= 5, -4.0, 1.0)
    rng = np.random.default_rng(0)
    y = 9 + weekly + 0.5 * np.sin(np.arange(days) / 58) + rng.normal(0, 0.3, days)
    return pd.DataFrame({"date": dates, "co2e_total_t": y,
                         "is_weekend": (dates.dayofweek >= 5).astype(int)})


def test_seasonal_naive_repeats_last_week():
    df = _series(60)
    fc = seasonal_naive(df, 14)
    assert np.allclose(fc["yhat"].iloc[:7], df["co2e_total_t"].iloc[-7:])
    assert np.allclose(fc["yhat"].iloc[7:], df["co2e_total_t"].iloc[-7:])
    assert (fc["hi90"] >= fc["yhat"]).all()


def test_score_is_zero_for_perfect_forecast():
    df = _series(120)
    train, actual = df.iloc[:90], df.iloc[90:]
    fc = actual.rename(columns={"co2e_total_t": "yhat"}).assign(lo90=lambda d: d.yhat - 1, hi90=lambda d: d.yhat + 1)
    m = _score(train, actual, fc)
    assert m["mae"] == pytest.approx(0) and m["coverage"] == 1 and m["days"] == 30


def test_lab_backtest_with_prophet_scores_against_actuals():
    df = _series(500)
    exp = run_experiment(df, ["prophet"], df["date"].iloc[399], train_days=365, horizon_days=91)
    assert len(exp.train) == 365 and exp.evaluated_days == 91
    run = exp.runs["prophet"]
    assert len(run.forecast) == 91 and set(run.metrics) >= {"mae", "mase", "coverage", "bias"}
    # The weekly cycle is easy: Prophet must beat the naive baseline
    assert run.metrics["mae"] < exp.naive.metrics["mae"]
    assert {"trend", "weekly"} <= set(run.details["components"].columns)


def test_lab_future_mode_has_nothing_to_score():
    df = _series(200)
    exp = run_experiment(df, ["prophet"], df["date"].iloc[-1], train_days=182, horizon_days=30)
    assert exp.evaluated_days == 0 and exp.runs["prophet"].metrics == {}


def test_lab_refuses_tiny_training_window():
    df = _series(100)
    with pytest.raises(ValueError):
        run_experiment(df, ["prophet"], df["date"].iloc[10], train_days=None, horizon_days=30)


def test_saved_upload_records_who_sent_it(tmp_path, monkeypatch):
    paths = {"root": tmp_path, "inbox": tmp_path / "inbox", "processed": tmp_path / "processed",
             "quarantine": tmp_path / "quarantine", "manifest": tmp_path / "uploads_manifest.json",
             "backup": tmp_path / "backup"}
    monkeypatch.setattr(intake, "_paths", lambda: paths)
    info = intake.inspect_upload("energie_2026-04.csv", FRENCH_ENERGY.encode())
    assert intake.save_uploads([info], uploaded_by="editor1") == [info.target_name]
    entry = intake.list_uploads()[-1]
    assert entry["uploaded_by"] == "editor1" and entry["department"] == "energie"
