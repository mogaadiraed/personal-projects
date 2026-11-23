"""End-to-end refresh: inbox CSVs → validation → DuckDB → daily features → forecast → backtest → alerts → reports.

* The forecast step uses the router (``router`` in config.yaml): Chronos-2 from ``min_days`` of history
  (60), Prophet below that or if Chronos-2 cannot run.
* The backtest is recomputed when the data changed since the last one (``backtest.refresh_after_upload``),
  so the model scores always describe the data the forecast was made on.
* The alerts are checked last, on the new forecast and backtest.

Usage:
    python -m carbonsync.pipeline            # full refresh
    python -m carbonsync.pipeline --no-report --no-backtest
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from carbonsync.obs import get_logger, setup_console_logging, step

log = get_logger("carbonsync.pipeline")  # stable name, also when run with -m


def get_project_root() -> Path:
    return Path(__file__).resolve().parents[2]


HISTORY_FILE = "pipeline_history.jsonl"


def _emissions_snapshot(db_path: Path) -> dict[str, Any]:
    import duckdb

    if not db_path.exists():
        return {"days": 0, "first": None, "last": None}
    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        row = conn.execute("SELECT COUNT(*), MIN(date), MAX(date) FROM emissions_daily").fetchone()
    except duckdb.Error:
        row = (0, None, None)
    finally:
        conn.close()
    return {"days": int(row[0] or 0), "first": str(row[1])[:10] if row[1] else None,
            "last": str(row[2])[:10] if row[2] else None}


def _backtest_through(root: Path) -> str | None:
    try:
        meta = json.loads((root / "data" / "backtest_summary.json").read_text(encoding="utf-8")).get("meta", {})
    except (OSError, json.JSONDecodeError, AttributeError):
        return None
    return meta.get("data_through")


def run_pipeline(forecast: bool = True, report: bool = True, trigger: str = "manual",
                 backtest: bool | None = None) -> dict[str, Any]:
    """Run the whole loop and return a summary (also appended to ``data/pipeline_history.jsonl``).

    ``backtest``: None follows ``backtest.refresh_after_upload`` (and skips it when the backtest already
    covers the latest day); True forces it; False skips it.
    """
    from carbonsync.features.daily import build_daily_features
    from carbonsync.ingest.load import load_config, load_processed_to_duckdb
    from carbonsync.ingest.validate import validate_all_inbox

    root = get_project_root()
    cfg = load_config()
    db_path = root / cfg.get("paths", {}).get("duckdb", "data/carbonsync.duckdb")
    t0 = time.perf_counter()
    summary: dict[str, Any] = {"started_at": datetime.now().isoformat(timespec="seconds"), "trigger": trigger, "steps": {}}

    log.info("━━━━ Pipeline start (%s) ━━━━", trigger)
    before = _emissions_snapshot(db_path)
    summary["before"] = before

    with step(log, "1/7 Validate inbox CSV files") as s:
        dq = validate_all_inbox()
    summary["steps"]["validate"] = {"seconds": round(s["seconds"], 2), **{k: dq[k] for k in ("total_files_scanned", "accepted_count", "rejected_count")}}

    with step(log, "2/7 Load accepted files into DuckDB") as s:
        loaded = load_processed_to_duckdb()
    summary["steps"]["load"] = {"seconds": round(s["seconds"], 2), "tables": loaded["tables_loaded"]}

    with step(log, "3/7 Rebuild daily table and emissions") as s:
        feats = build_daily_features()
    summary["steps"]["features"] = {"seconds": round(s["seconds"], 2), **{k: feats[k] for k in (
        "total_days", "imputed_days", "data_through", "department_last_dates", "pending_departments")}}

    if forecast:
        from carbonsync.models.ensemble import run_and_save_forecast
        from carbonsync.models.router import router_rule

        horizon = int(cfg.get("forecast", {}).get("prediction_length", 183))
        with step(log, f"4/7 Forecast with the router ({router_rule(cfg)['en']})") as s:
            res = run_and_save_forecast(model_name="auto", horizon_days=horizon)
        summary["steps"]["forecast"] = {"seconds": round(s["seconds"], 2), "run_id": res["run_id"],
                                        "model": res["model_name"], "history_days": res["history_days"]}
    else:
        log.info("4/7 Forecast skipped")

    bt_cfg = cfg.get("backtest", {})
    data_through = summary["steps"]["features"].get("data_through")
    want_bt = bool(bt_cfg.get("refresh_after_upload", True)) if backtest is None else backtest
    if want_bt and backtest is None and data_through and _backtest_through(root) == str(data_through)[:10]:
        log.info("5/7 Backtest already covers %s: kept", str(data_through)[:10])
        summary["steps"]["backtest"] = {"skipped": "up to date", "data_through": str(data_through)[:10]}
    elif want_bt:
        from carbonsync.backtest.rolling import refresh_backtest

        with step(log, "5/7 Refresh the backtest on the new data") as s:
            try:
                bt = refresh_backtest()
            except Exception as err:  # noqa: BLE001 - the forecast and reports must still be produced
                log.warning("Backtest refresh failed: %s", err)
                bt = {"error": f"{type(err).__name__}: {err}"}
        summary["steps"]["backtest"] = {"seconds": round(s["seconds"], 2), **bt}
    else:
        log.info("5/7 Backtest skipped")

    from carbonsync import alerts

    with step(log, "6/7 Check alerts") as s:
        try:
            al = alerts.counts(alerts.check(trigger=trigger))
        except Exception as err:  # noqa: BLE001 - alerts must never block the pipeline
            log.warning("Alert check failed: %s", err)
            al = {"error": f"{type(err).__name__}: {err}"}
    summary["steps"]["alerts"] = {"seconds": round(s["seconds"], 2), **al}

    if report:
        from carbonsync.export.brief import generate_pdf_brief
        from carbonsync.export.insights import build_insights
        from carbonsync.export.report import generate_excel_report

        language = cfg.get("app", {}).get("language", "fr")
        with step(log, "7/7 Generate reports (Excel audit workbook + PDF brief)") as s:
            ins = build_insights(language)
            path = generate_excel_report(lang=language, insights=ins)
            brief = generate_pdf_brief(lang=language, insights=ins)
        summary["steps"]["report"] = {"seconds": round(s["seconds"], 2), "file": path.name, "brief": brief.name}
    else:
        log.info("7/7 Report skipped")

    after = _emissions_snapshot(db_path)
    summary["after"] = after
    summary["new_days"] = after["days"] - before["days"]
    summary["seconds"] = round(time.perf_counter() - t0, 2)
    log.info("━━━━ Pipeline done in %.1f s · %d → %d days (%+d) · data through %s ━━━━",
             summary["seconds"], before["days"], after["days"], summary["new_days"], after["last"])

    hist = root / "data" / HISTORY_FILE
    with open(hist, "a", encoding="utf-8") as f:
        f.write(json.dumps(summary, default=str) + "\n")
    return summary


def read_history(limit: int = 20) -> list[dict[str, Any]]:
    hist = get_project_root() / "data" / HISTORY_FILE
    if not hist.exists():
        return []
    lines = hist.read_text(encoding="utf-8").strip().splitlines()
    out = []
    for line in reversed(lines[-limit:]):
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Refresh CarbonSync data, forecast and report.")
    parser.add_argument("--no-forecast", action="store_true")
    parser.add_argument("--no-report", action="store_true")
    parser.add_argument("--no-backtest", action="store_true")
    parser.add_argument("--backtest", action="store_true", help="recompute the backtest even if up to date")
    args = parser.parse_args()
    setup_console_logging()
    run_pipeline(forecast=not args.no_forecast, report=not args.no_report, trigger="cli",
                 backtest=False if args.no_backtest else (True if args.backtest else None))
