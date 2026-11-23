"""
CarbonSync AI – CSV Ingestion & Validation Pipeline
====================================================
Validates incoming department CSV exports:
  1. Detects encoding (UTF-8, UTF-8 BOM, Latin-1)
  2. Detects delimiter (';', ',', '\t') and decimal separator (',', '.')
  3. Strips whitespace, blank rows, and comment headers
  4. Maps column aliases (e.g. 'Electricité (kWh)' -> 'electricity_kwh')
  5. Normalizes dates (ISO YYYY-MM-DD and DD/MM/YYYY)
  6. Enforces business rules:
     - Deduplicates identical date entries (keeps latest)
     - Detects unit slips (MWh vs kWh) and converts with warning
     - Replaces negative consumption values with NaN and logs warning
     - Rejects files with missing required columns or >10% total missingness
  7. Validates against Pandera schemas
  8. Routes:
     - Passed files -> data/processed/<department>/<file>.parquet
     - Rejected files -> data/quarantine/<file> + <file>.errors.json
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pandera.pandas as pa
import yaml

from carbonsync.ingest.schemas import EXPECTED_COLUMNS, SCHEMAS
from carbonsync.obs import get_logger, step

log = get_logger(__name__)


def get_project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def load_config(config_path: Path | None = None) -> dict[str, Any]:
    if config_path is None:
        config_path = get_project_root() / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# Column alias mapping for French department exports
COLUMN_ALIASES: dict[str, str] = {
    "electricite (kwh)": "electricity_kwh",
    "electricité (kwh)": "electricity_kwh",
    "electricite_kwh": "electricity_kwh",
    "fioul (l)": "fuel_oil_l",
    "fioul_l": "fuel_oil_l",
    "quantite produite": "units_produced",
    "quantité produite": "units_produced",
    "unites_produites": "units_produced",
    "heures_fonctionnement": "operating_hours",
    "arret_flag": "shutdown_flag",
    "nombre de trajets": "trips",
    "nombre_trajets": "trips",
    "gazole (l)": "diesel_l",
    "gazole_l": "diesel_l",
    "dechets (kg)": "waste_kg",
    "déchets (kg)": "waste_kg",
    "dechets_kg": "waste_kg",
    "eau (m3)": "water_m3",
    "eau_m3": "water_m3",
    "date": "date",
}


@dataclass
class ValidationResult:
    file_name: str
    department: str
    success: bool
    rows_count: int = 0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    suggested_fix: str = ""
    processed_path: str = ""
    quarantine_path: str = ""


def detect_department(file_name: str) -> str | None:
    """Infers department name from file name (e.g. 'energie_2022-04.csv' -> 'energie')."""
    lower = file_name.lower()
    if lower.startswith("energie"):
        return "energie"
    if lower.startswith("production"):
        return "production"
    if lower.startswith("logistique"):
        return "logistique"
    if lower.startswith(("dechets_eau", "dechets")):
        return "dechets_eau"
    return None


_ISO_DATE = r"^\d{4}-\d{1,2}-\d{1,2}"


def parse_dates(values: pd.Series, errors: str = "raise") -> pd.Series:
    """Parse department dates: ISO ``YYYY-MM-DD`` strictly, anything else as day-first (``DD/MM/YYYY``).

    ``format="mixed", dayfirst=True`` on its own reads ``2025-03-05`` as 3 May, so ISO rows
    must be parsed separately.
    """
    s = values.astype(str).str.strip()
    iso = s.str.match(_ISO_DATE)
    out = pd.Series(pd.NaT, index=s.index, dtype="datetime64[ns]")
    if iso.any():
        out[iso] = pd.to_datetime(s[iso].str[:10], format="%Y-%m-%d", errors="coerce")
    if (~iso).any():
        out[~iso] = pd.to_datetime(s[~iso], format="mixed", dayfirst=True, errors="coerce")
    if errors == "raise" and out.isna().any():
        bad = s[out.isna()].head(3).tolist()
        raise ValueError(f"{int(out.isna().sum())} unparseable date(s), e.g. {bad}")
    return out


def detect_encoding_and_content(file_path: Path) -> tuple[str, list[str]]:
    """Tries UTF-8, UTF-8-sig, and Latin-1 to decode file into lines."""
    for enc in ["utf-8-sig", "utf-8", "latin-1"]:
        try:
            with open(file_path, "r", encoding=enc) as f:
                lines = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]
            return enc, lines
        except (UnicodeDecodeError, UnicodeError):
            continue
    # Fallback with replacement
    with open(file_path, "r", encoding="utf-8", errors="replace") as f:
        lines = [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]
    return "utf-8", lines


def detect_format(lines: list[str]) -> tuple[str, str]:
    """Detects delimiter (';', ',', '\t') and decimal separator (',', '.')."""
    if not lines:
        return ",", "."
    header = lines[0]
    counts = {";": header.count(";"), ",": header.count(","), "\t": header.count("\t")}
    sep = max(counts, key=counts.get)  # type: ignore
    if counts[sep] == 0:
        sep = ","

    # Determine decimal separator
    dec = "."
    if sep == ";":
        # Check sample data lines for commas in numbers
        for line in lines[1:5]:
            parts = line.split(";")
            for part in parts:
                if re.match(r"^-?\d+,\d+$", part.strip()):
                    dec = ","
                    break
    return sep, dec


def validate_file(file_path: Path, config: dict[str, Any] | None = None) -> ValidationResult:
    """Validates and processes a single department CSV file."""
    root = get_project_root()
    if config is None:
        config = load_config()

    paths_cfg = config.get("paths", {})
    processed_dir = root / paths_cfg.get("processed", "data/processed")
    quarantine_dir = root / paths_cfg.get("quarantine", "data/quarantine")

    dept = detect_department(file_path.name)
    if dept is None or dept not in SCHEMAS:
        return ValidationResult(
            file_name=file_path.name,
            department="unknown",
            success=False,
            errors=[f"Unknown department in file name '{file_path.name}'"],
            suggested_fix="Rename file to start with energie_, production_, logistique_, or dechets_eau_",
        )

    expected_cols = EXPECTED_COLUMNS[dept]
    schema = SCHEMAS[dept]
    warnings: list[str] = []
    errors: list[str] = []

    # 1. Read file with encoding & delimiter detection
    _encoding, lines = detect_encoding_and_content(file_path)
    if not lines or len(lines) < 2:
        errors.append("File is empty or contains no data rows.")
        return _quarantine(file_path, dept, errors, warnings, quarantine_dir, "Send a non-empty CSV export")

    sep, dec = detect_format(lines)
    if dec == ",":
        warnings.append("French number format detected (';' delimiter, ',' decimals) – converted.")

    # 2. Parse into DataFrame
    try:
        from io import StringIO

        raw_text = "\n".join(lines)
        df = pd.read_csv(StringIO(raw_text), sep=sep, decimal=dec, dtype=str)
    except (ValueError, TypeError, pd.errors.ParserError) as e:
        errors.append(f"Failed to parse CSV: {e}")
        return _quarantine(file_path, dept, errors, warnings, quarantine_dir, "Check CSV formatting and delimiter")

    # 3. Strip whitespace from headers and values
    df.columns = [c.strip() for c in df.columns]
    for c in df.columns:
        df[c] = df[c].str.strip()

    # 4. Map Column Aliases
    renamed: dict[str, str] = {}
    for col in df.columns:
        norm = col.lower().strip()
        if norm in COLUMN_ALIASES:
            renamed[col] = COLUMN_ALIASES[norm]
    if renamed:
        warnings.append(f"Mapped column aliases: {renamed}")
        df = df.rename(columns=renamed)

    # 5. Check Required Columns
    missing_cols = [c for c in expected_cols if c not in df.columns]
    if missing_cols:
        errors.append(f"Missing required columns for {dept}: {missing_cols}")
        return _quarantine(
            file_path,
            dept,
            errors,
            warnings,
            quarantine_dir,
            f"Include required columns {expected_cols}",
        )

    # Keep only expected columns
    df = df[expected_cols].copy()

    # 6. Normalize Dates
    try:
        df["date"] = parse_dates(df["date"])
    except (ValueError, TypeError) as e:
        errors.append(f"Invalid date format in date column: {e}")
        return _quarantine(file_path, dept, errors, warnings, quarantine_dir, "Use YYYY-MM-DD or DD/MM/YYYY date format")

    # 7. Check & Clean Duplicates
    if df["date"].duplicated().any():
        dup_count = int(df["date"].duplicated().sum())
        warnings.append(f"Found {dup_count} duplicate date rows; keeping the latest occurrence.")
        df = df.drop_duplicates(subset=["date"], keep="last")

    # Sort chronologically
    df = df.sort_values("date").reset_index(drop=True)

    # 8. Numeric Coercion & Outlier Handling
    for c in expected_cols:
        if c == "date":
            continue
        # Convert strings to float/numeric
        # Values are read as text, so the decimal separator must be normalised here
        # (read_csv's ``decimal=`` is ignored with ``dtype=str``).
        txt = df[c].astype(str).str.replace("[ \u00a0\u202f]", "", regex=True)  # spaces, NBSP, narrow NBSP
        if dec == ",":
            txt = txt.str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
        df[c] = pd.to_numeric(txt, errors="coerce")

        # Check negative values (physical measurements cannot be negative)
        negative_mask = df[c] < 0
        if negative_mask.any():
            neg_count = int(negative_mask.sum())
            warnings.append(f"Found {neg_count} negative value(s) in column '{c}'. Converted to NaN for imputation.")
            df.loc[negative_mask, c] = np.nan

    # 9. Unit Sanity Check (e.g. MWh vs kWh in electricity)
    if dept == "energie" and "electricity_kwh" in df.columns:
        valid_elec = df["electricity_kwh"].dropna()
        if len(valid_elec) > 0:
            median_elec = float(valid_elec.median())
            # Typical daily electricity is 5,000 to 25,000 kWh. If median < 50, it was exported as MWh!
            if 0 < median_elec < 100.0:
                warnings.append(
                    f"Unit slip detected: median electricity is {median_elec:.2f} (likely MWh). Auto-scaling by 1000 to kWh."
                )
                df["electricity_kwh"] = df["electricity_kwh"] * 1000.0

    # 10. Completeness Check (>10% missing in non-shutdown columns is rejected)
    for c in expected_cols:
        if c in ("date", "shutdown_flag"):
            continue
        missing_pct = df[c].isna().mean()
        if missing_pct > 0.15:  # more than 15% missing
            errors.append(f"Column '{c}' has {missing_pct:.1%} missing values (exceeds 15% threshold).")
            return _quarantine(
                file_path,
                dept,
                errors,
                warnings,
                quarantine_dir,
                f"Re-export department data with complete records for '{c}'",
            )

    # 11. Pandera Schema Validation
    try:
        schema.validate(df, lazy=True)
    except pa.errors.SchemaErrors as err:
        errors.append(f"Pandera schema check failed: {err.failure_cases[['column', 'check', 'failure_case']].to_dict(orient='records')}")
        return _quarantine(file_path, dept, errors, warnings, quarantine_dir, "Fix values violating range and schema rules")

    # 12. Success -> Save to processed Parquet
    dept_dir = processed_dir / dept
    dept_dir.mkdir(parents=True, exist_ok=True)
    out_parquet = dept_dir / f"{file_path.stem}.parquet"
    df.to_parquet(out_parquet, index=False)

    # A corrected re-upload replaces an earlier rejection of the same file
    for stale in (quarantine_dir / file_path.name, quarantine_dir / f"{file_path.name}.errors.json"):
        if stale.exists():
            stale.unlink()
            log.info("Cleared previous quarantine entry for %s", file_path.name)

    return ValidationResult(
        file_name=file_path.name,
        department=dept,
        success=True,
        rows_count=len(df),
        warnings=warnings,
        processed_path=str(out_parquet.relative_to(root) if out_parquet.is_relative_to(root) else out_parquet),
    )


def _quarantine(
    file_path: Path,
    dept: str,
    errors: list[str],
    warnings: list[str],
    quarantine_dir: Path,
    suggested_fix: str,
) -> ValidationResult:
    """Helper to copy rejected file to quarantine and save JSON error report."""
    root = get_project_root()
    quarantine_dir.mkdir(parents=True, exist_ok=True)

    # If an earlier version of this file had been accepted, it must no longer feed the database
    if dept in SCHEMAS:
        processed_dir = root / load_config().get("paths", {}).get("processed", "data/processed")
        stale = processed_dir / dept / f"{file_path.stem}.parquet"
        if stale.exists():
            stale.unlink()
            log.warning("Removed previously accepted version of %s", file_path.name)

    # Copy original file to quarantine
    quarantined_file = quarantine_dir / file_path.name
    quarantined_file.write_bytes(file_path.read_bytes())

    # Write error report
    error_report = {
        "file_name": file_path.name,
        "department": dept,
        "rejected_at": datetime.now(UTC).isoformat(),
        "errors": errors,
        "warnings": warnings,
        "suggested_fix": suggested_fix,
    }
    report_file = quarantine_dir / f"{file_path.name}.errors.json"
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(error_report, f, indent=2)

    return ValidationResult(
        file_name=file_path.name,
        department=dept,
        success=False,
        errors=errors,
        warnings=warnings,
        suggested_fix=suggested_fix,
        quarantine_path=str(quarantined_file.relative_to(root) if quarantined_file.is_relative_to(root) else quarantined_file),
    )


def validate_all_inbox(config_path: Path | None = None) -> dict[str, Any]:
    """Validates all CSV files in data/inbox/."""
    root = get_project_root()
    cfg = load_config(config_path)
    inbox_dir = root / cfg.get("paths", {}).get("inbox", "data/inbox")

    csv_files = sorted(inbox_dir.glob("*.csv"))
    accepted = []
    rejected = []
    total_warnings = 0

    with step(log, f"Validating {len(csv_files)} CSV files in {inbox_dir.name}/"):
        for f in csv_files:
            res = validate_file(f, config=cfg)
            if res.success:
                accepted.append(res)
                total_warnings += len(res.warnings)
                log.debug("accepted %s (%d rows)", f.name, res.rows_count)
            else:
                rejected.append(res)
                log.debug("rejected %s: %s", f.name, "; ".join(res.errors)[:160])
    log.info("Validation: %d accepted · %d quarantined · %d repair warnings",
             len(accepted), len(rejected), total_warnings)

    summary = {
        "total_files_scanned": len(csv_files),
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "total_warnings": total_warnings,
        "rejected_files": [r.file_name for r in rejected],
    }

    # Save summary report for Streamlit Data Quality page
    dq_report_file = root / "data" / "data_quality_report.json"
    with open(dq_report_file, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)

    return summary


if __name__ == "__main__":
    print("Running CSV ingestion and validation pipeline...")
    res = validate_all_inbox()
    print(
        f"Validation complete: {res['total_files_scanned']} files scanned. "
        f"{res['accepted_count']} accepted, {res['rejected_count']} quarantined."
    )
