"""The pipeline runs in the background and the Overview never shows a forecast older than the data.

Bug fixed (2026-11-23): uploading Q2 2026 ran steps 1-3 (data through 2026-06-30) but the run was stopped
before the forecast when the page was left, so the Overview kept the forecast made from data up to 2026-03-31,
drawn over April-June actuals.
"""
from __future__ import annotations

import threading
import time

import duckdb
import pandas as pd
import pytest

from app.core import jobs
from app.data.loader import pick_active_run
from carbonsync import db
from carbonsync.obs import get_logger


# ---------------------------------------------------------------------------
# One access mode per process while the dashboard runs
# ---------------------------------------------------------------------------
@pytest.fixture
def shared_flag():
    before = db._SHARED
    yield
    db.shared_process(before)


def test_reader_and_writer_coexist_in_the_dashboard(tmp_path, shared_flag):
    path = tmp_path / "t.duckdb"
    with duckdb.connect(str(path)) as c:
        c.execute("CREATE TABLE t AS SELECT 1 AS x")

    db.shared_process(False)                       # command line: modes kept, DuckDB refuses the mix
    reader = db.connect(path, read_only=True)
    with pytest.raises(duckdb.ConnectionException):
        db.connect(path)
    reader.close()

    db.shared_process(True)                        # dashboard: a page reads while the pipeline writes
    reader = db.connect(path, read_only=True)
    writer = db.connect(path)
    writer.execute("INSERT INTO t VALUES (2)")
    assert reader.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 2
    reader.close()
    writer.close()


def test_read_only_on_a_missing_file_does_not_create_it(tmp_path, shared_flag):
    db.shared_process(True)
    missing = tmp_path / "none.duckdb"
    with pytest.raises(duckdb.Error):
        db.connect(missing, read_only=True)
    assert not missing.exists()


# ---------------------------------------------------------------------------
# Which forecast the pages show
# ---------------------------------------------------------------------------
LAST = pd.Timestamp("2026-06-30")


def _runs(*rows) -> pd.DataFrame:
    df = pd.DataFrame(rows, columns=["run_id", "model_name", "end_date", "created_at"])
    df["created_at"] = pd.to_datetime(df["created_at"])
    return df.sort_values("created_at", ascending=False).reset_index(drop=True)


OLD = ("chronos_old", "chronos", "2026-03-31", "2026-11-21 03:02")
NEW = ("chronos_new", "chronos", "2026-06-30", "2026-11-23 17:00")


def test_forecast_older_than_the_data_is_flagged():
    meta = pick_active_run(_runs(OLD), LAST, "chronos")
    assert meta["run_id"] == "chronos_old" and meta["stale"] and not meta["has_current"]


def test_current_forecast_wins_over_an_older_pick():
    runs = _runs(OLD, NEW)
    assert pick_active_run(runs, LAST, "chronos")["run_id"] == "chronos_new"
    # picked on the Forecast page before the Q2 upload (data then ran to 31 March): no longer applies
    meta = pick_active_run(runs, LAST, "chronos", chosen="chronos_old", chosen_through="2026-03-31")
    assert meta["run_id"] == "chronos_new" and not meta["stale"]
    # an older session pick without a date: kept only if it is current
    assert pick_active_run(runs, LAST, "chronos", chosen="chronos_old")["run_id"] == "chronos_new"


def test_older_run_picked_on_the_current_data_is_kept_and_labelled():
    meta = pick_active_run(_runs(OLD, NEW), LAST, "chronos", chosen="chronos_old", chosen_through="2026-06-30")
    assert meta["run_id"] == "chronos_old" and meta["stale"] and meta["has_current"]


# ---------------------------------------------------------------------------
# Background runs
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _no_job_left():
    """The job slot is shared by the whole process: later page tests must not see these fake runs."""
    yield
    _wait()
    with jobs._LOCK:
        jobs._JOB.clear()


def _wait(timeout: float = 10.0) -> dict:
    end = time.monotonic() + timeout
    while jobs.running() and time.monotonic() < end:
        time.sleep(0.05)
    return jobs.snapshot()


def test_background_job_finishes_and_keeps_its_log():
    release = threading.Event()
    log = get_logger("pipeline")

    def work():
        log.info("4/7 Forecast with the router…")
        release.wait(5)
        return {"steps": {"forecast": {"run_id": "r1"}}}

    assert jobs.start(work, "upload", "test")
    assert jobs.running()
    assert not jobs.start(dict, "manual")      # one run at a time
    release.set()
    job = _wait()
    assert not job["running"] and job["error"] is None
    assert job["summary"]["steps"]["forecast"]["run_id"] == "r1"
    assert job["step"] == "4/7 Forecast with the router…"
    assert any("Forecast with the router" in line for line in job["lines"])


def test_background_job_failure_is_reported():
    def work():
        raise RuntimeError("Chronos-2 weights missing")

    assert jobs.start(work, "manual")
    job = _wait()
    assert not job["running"] and job["summary"] is None
    assert job["error"] == "RuntimeError: Chronos-2 weights missing"
