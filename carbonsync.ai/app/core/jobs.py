"""Pipeline runs in a background thread.

Streamlit stops a page's script as soon as the user clicks elsewhere or the page reloads. A pipeline run inside
the page (upload → validate → load → daily table → forecast → backtest → alerts → reports) was therefore cut off
halfway when the user left the Data intake page, leaving new data without a new forecast. Here the run lives in
its own thread: it always finishes, any page can follow it, and the caches are cleared at the end.

One job at a time for the whole dashboard (there is one database). The database is shared safely with the pages
through ``carbonsync.db`` (see ``shared_process``).
"""
from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any

from carbonsync.obs import capture, get_logger

log = get_logger(__name__)

_LOCK = threading.Lock()
_JOB: dict[str, Any] = {}
MAX_LINES = 400


def snapshot() -> dict[str, Any]:
    """A copy of the current (or last) job: id, label, trigger, running, step, lines, summary, error, times."""
    with _LOCK:
        job = dict(_JOB)
        job["lines"] = list(_JOB.get("lines", []))
    return job


def running() -> bool:
    with _LOCK:
        return bool(_JOB.get("running"))


def start(work: Callable[[], dict[str, Any]], trigger: str, label: str = "") -> bool:
    """Starts ``work`` in the background; False when a job is already running."""
    with _LOCK:
        if _JOB.get("running"):
            return False
        _JOB.clear()
        _JOB.update(id=uuid.uuid4().hex[:8], label=label, trigger=trigger, running=True, step="", lines=[],
                    summary=None, error=None, started_at=datetime.now().isoformat(timespec="seconds"),
                    finished_at=None)
        job_id = _JOB["id"]
    threading.Thread(target=_run, args=(job_id, work), daemon=True, name=f"carbonsync-job-{job_id}").start()
    return True


def start_pipeline(trigger: str, label: str = "", report: bool = True,
                   before: Callable[[], list[str]] | None = None) -> bool:
    """The whole pipeline in the background. ``before`` runs first in the same thread (save the uploads,
    remove them for a reset) and returns the file names it saved, shown in the result."""
    def work() -> dict[str, Any]:
        from carbonsync.pipeline import run_pipeline

        saved = before() if before else []
        summary = run_pipeline(forecast=True, report=report, trigger=trigger)
        summary["saved"] = saved or []
        return summary

    return start(work, trigger, label)


def _run(job_id: str, work: Callable[[], dict[str, Any]]) -> None:
    me = threading.get_ident()

    def on_record(record: logging.LogRecord, line: str) -> None:
        if record.thread != me:          # page reads by other sessions are not part of this run's log
            return
        mark = "! " if record.levelno >= logging.WARNING else "  "
        msg = record.getMessage()
        with _LOCK:
            if _JOB.get("id") != job_id:
                return
            _JOB["lines"].append(mark + line)
            del _JOB["lines"][:-MAX_LINES]
            if msg.endswith("…"):
                _JOB["step"] = msg

    summary, error = None, None
    try:
        with capture(on_record):
            summary = work()
    except Exception as exc:  # reported on the pages, never lost
        log.exception("Background pipeline failed")
        error = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            import streamlit as st

            st.cache_data.clear()        # every page re-reads the database
        except Exception as exc:  # noqa: BLE001 - outside Streamlit (tests)
            log.debug("Cache clear skipped: %s", exc)
        with _LOCK:
            if _JOB.get("id") == job_id:
                _JOB.update(running=False, summary=summary, error=error,
                            finished_at=datetime.now().isoformat(timespec="seconds"))
