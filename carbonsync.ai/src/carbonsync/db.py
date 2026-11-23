"""One way to open the DuckDB file, for the pipeline and the dashboard alike.

Inside one process, DuckDB refuses a read-only and a read-write connection to the same file at the same time
("Can't open a connection to same database file with a different configuration"). The dashboard reads the
database while the pipeline writes it in a background thread (``app/core/jobs.py``), so the dashboard calls
``shared_process()`` at start-up: every connection is then opened read-write, all of them share one database,
and DuckDB isolates the readers from the writer. Elsewhere (command line, tests) ``read_only`` is kept as asked.
"""
from __future__ import annotations

from pathlib import Path

import duckdb

_SHARED = False


def shared_process(on: bool = True) -> None:
    """Called by the dashboard: readers and the background pipeline share this process."""
    global _SHARED
    _SHARED = on


def connect(path: str | Path, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """``duckdb.connect`` with one access mode per process when the dashboard runs.

    A read-only request on a missing file stays read-only, so it fails as before instead of creating an empty
    database."""
    if read_only and _SHARED and Path(path).exists():
        read_only = False
    return duckdb.connect(str(path), read_only=read_only)
