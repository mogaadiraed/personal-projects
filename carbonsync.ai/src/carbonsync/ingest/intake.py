"""Manual data intake: inspect uploaded department CSVs, route them to the inbox, undo uploads.

Every saved upload is recorded in ``data/uploads_manifest.json``. When an upload
replaces an existing inbox file, the original is kept in ``data/uploads_backup/``
so ``remove_uploads()`` can restore the dataset exactly as it was.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from carbonsync.ingest.schemas import EXPECTED_COLUMNS
from carbonsync.ingest.validate import (
    COLUMN_ALIASES,
    detect_department,
    detect_format,
    parse_dates,
)
from carbonsync.obs import get_logger

log = get_logger(__name__)

DEPARTMENTS = ("energie", "production", "logistique", "dechets_eau")
MANIFEST = "uploads_manifest.json"
BACKUP_DIR = "uploads_backup"


def get_project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _paths() -> dict[str, Path]:
    from carbonsync.ingest.load import load_config

    cfg = load_config().get("paths", {})
    root = get_project_root()
    return {
        "root": root,
        "inbox": root / cfg.get("inbox", "data/inbox"),
        "processed": root / cfg.get("processed", "data/processed"),
        "quarantine": root / cfg.get("quarantine", "data/quarantine"),
        "manifest": root / "data" / MANIFEST,
        "backup": root / "data" / BACKUP_DIR,
    }


# ---------------------------------------------------------------------------
# Inspection
# ---------------------------------------------------------------------------
@dataclass
class UploadInfo:
    original_name: str
    target_name: str
    department: str | None
    detected_from: str  # "name" | "columns" | "chosen" | "-"
    rows: int = 0
    first_date: str | None = None
    last_date: str | None = None
    replaces_existing: bool = False
    notes: list[str] = field(default_factory=list)
    ok: bool = True
    content: bytes = field(default=b"", repr=False)

    def public(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("content")
        return d


def _decode(content: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return content.decode(enc)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _normalised_columns(header: list[str]) -> set[str]:
    out = set()
    for col in header:
        norm = col.strip().strip('"').lower()
        out.add(COLUMN_ALIASES.get(norm, norm))
    return out


def detect_from_columns(content: bytes) -> str | None:
    """Department whose required columns are all present in the header."""
    lines = [ln for ln in _decode(content).splitlines() if ln.strip() and not ln.strip().startswith("#")]
    if not lines:
        return None
    sep, _ = detect_format(lines)
    cols = _normalised_columns(lines[0].split(sep))
    scores = {d: len(set(EXPECTED_COLUMNS[d]) & cols) for d in DEPARTMENTS}
    best = max(scores, key=scores.get)
    needed = len(EXPECTED_COLUMNS[best])
    return best if scores[best] == needed else (best if scores[best] >= needed - 1 and scores[best] > 1 else None)


def _quick_profile(content: bytes) -> tuple[int, str | None, str | None]:
    lines = [ln for ln in _decode(content).splitlines() if ln.strip() and not ln.strip().startswith("#")]
    if len(lines) < 2:
        return 0, None, None
    sep, dec = detect_format(lines)
    try:
        df = pd.read_csv(io.StringIO("\n".join(lines)), sep=sep, decimal=dec, dtype=str)
    except (ValueError, pd.errors.ParserError):
        return len(lines) - 1, None, None
    date_col = next((c for c in df.columns if c.strip().lower() == "date"), None)
    if date_col is None:
        return len(df), None, None
    dates = parse_dates(df[date_col], errors="coerce").dropna()
    if dates.empty:
        return len(df), None, None
    return len(df), str(dates.min().date()), str(dates.max().date())


_SAFE = re.compile(r"[^A-Za-z0-9_.\-]+")


def inspect_upload(name: str, content: bytes, department: str | None = None) -> UploadInfo:
    """Work out which department a file belongs to and what it will be saved as."""
    base = _SAFE.sub("_", Path(name).name)
    if not base.lower().endswith(".csv"):
        return UploadInfo(name, base, None, "-", ok=False, notes=["Not a .csv file"])

    by_name = detect_department(base)
    by_cols = detect_from_columns(content)
    notes: list[str] = []

    if department:
        dept, source = department, "chosen"
        if by_cols and by_cols != department:
            notes.append(f"Columns look like '{by_cols}', not '{department}' – validation will likely reject it")
    elif by_name:
        dept, source = by_name, "name"
        if by_cols and by_cols != by_name:
            notes.append(f"File name says '{by_name}' but columns look like '{by_cols}'")
    elif by_cols:
        dept, source = by_cols, "columns"
    else:
        return UploadInfo(name, base, None, "-", ok=False,
                          notes=["Department not recognised (name prefix or columns)"], content=content)

    target = base if base.lower().startswith(dept) else f"{dept}_{base}"
    rows, first, last = _quick_profile(content)
    if rows == 0:
        notes.append("No data rows")
    exists = (_paths()["inbox"] / target).exists()
    if exists:
        notes.append("Replaces a file already in the inbox")
    return UploadInfo(name, target, dept, source, rows, first, last, exists, notes, ok=rows > 0, content=content)


def expand_archive(name: str, content: bytes) -> list[tuple[str, bytes]]:
    """Return ``[(name, bytes)]`` for a CSV, or every CSV inside a .zip."""
    if name.lower().endswith(".zip"):
        out = []
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            for info in zf.infolist():
                inner = Path(info.filename).name
                if not info.is_dir() and inner.lower().endswith(".csv") and not inner.startswith("._"):
                    out.append((inner, zf.read(info)))
        log.debug("Archive %s: %d CSV files", name, len(out))
        return out
    return [(name, content)]


# ---------------------------------------------------------------------------
# Saving / undo
# ---------------------------------------------------------------------------
def _read_manifest() -> list[dict[str, Any]]:
    p = _paths()["manifest"]
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def _write_manifest(entries: list[dict[str, Any]]) -> None:
    p = _paths()["manifest"]
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(entries, indent=2), encoding="utf-8")


def save_uploads(items: list[UploadInfo], uploaded_by: str | None = None) -> list[str]:
    """Write accepted-for-intake files to the inbox and record them (and who sent them) in the manifest."""
    paths = _paths()
    paths["inbox"].mkdir(parents=True, exist_ok=True)
    manifest = _read_manifest()
    known = {e["file"] for e in manifest}
    saved = []
    for it in items:
        if not it.ok or not it.department:
            continue
        dest = paths["inbox"] / it.target_name
        if dest.exists() and it.target_name not in known:
            # Keep the original so the upload can be undone
            paths["backup"].mkdir(parents=True, exist_ok=True)
            shutil.copy2(dest, paths["backup"] / it.target_name)
        dest.write_bytes(it.content)
        manifest = [e for e in manifest if e["file"] != it.target_name]
        manifest.append({
            "file": it.target_name,
            "department": it.department,
            "original_name": it.original_name,
            "rows": it.rows,
            "first_date": it.first_date,
            "last_date": it.last_date,
            "sha256": hashlib.sha256(it.content).hexdigest()[:16],
            "uploaded_at": datetime.now().isoformat(timespec="seconds"),
            "uploaded_by": uploaded_by or None,
        })
        saved.append(it.target_name)
        log.info("Upload saved → inbox/%s (%s, %d rows, %s → %s)", it.target_name, it.department,
                 it.rows, it.first_date, it.last_date)
    _write_manifest(manifest)
    return saved


def list_uploads() -> list[dict[str, Any]]:
    return _read_manifest()


def remove_uploads() -> list[str]:
    """Delete every manually uploaded file (inbox, processed, quarantine) and restore replaced originals."""
    paths = _paths()
    removed = []
    for entry in _read_manifest():
        name, dept = entry["file"], entry["department"]
        stem = Path(name).stem
        for f in (paths["inbox"] / name, paths["processed"] / dept / f"{stem}.parquet",
                  paths["quarantine"] / name, paths["quarantine"] / f"{name}.errors.json"):
            if f.exists():
                f.unlink()
        backup = paths["backup"] / name
        if backup.exists():
            shutil.move(str(backup), str(paths["inbox"] / name))
            log.info("Restored original inbox/%s", name)
        removed.append(name)
    _write_manifest([])
    log.info("Removed %d uploaded files", len(removed))
    return removed
