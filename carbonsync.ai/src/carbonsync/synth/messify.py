"""
CarbonSync AI – CSV Messifier
==============================
Takes clean synthetic CSV files in data/inbox/ and injects realistic defects:
  - French Excel format (;-delimiter, ,-decimal)
  - Date format variants (DD/MM/YYYY)
  - Encodings (Latin-1, UTF-8 BOM)
  - Duplicate rows
  - Missing column (quarantine candidate)
  - Renamed / alias columns
  - Unit slips (MWh instead of kWh)
  - Negative values
  - Blank rows and trailing spaces
  - Late correction duplicate exports

Target: ~10% of files carry at least one defect; ~2% are unusable (quarantine).
Produces: data/messy_manifest.json (detailed record of injected defects).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml


def get_project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def load_config(config_path: Path | None = None) -> dict[str, Any]:
    if config_path is None:
        config_path = get_project_root() / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def messify_inbox(config_path: Path | None = None) -> dict[str, Any]:
    """Applies realistic defects to clean department CSV files in data/inbox/."""
    root = get_project_root()
    cfg = load_config(config_path)

    gen_cfg = cfg.get("generator", {})
    paths_cfg = cfg.get("paths", {})
    seed = gen_cfg.get("seed", 42) + 999

    inbox_dir = root / paths_cfg.get("inbox", "data/inbox")
    files = sorted(inbox_dir.glob("*.csv"))
    n_files = len(files)

    if n_files == 0:
        raise FileNotFoundError(f"No CSV files found in {inbox_dir}. Run generator first.")

    rng = np.random.default_rng(seed)

    # 10% messy files (~38 files), ~2% unusable (~8 files)
    messy_rate = float(gen_cfg.get("messy_file_rate", 0.10))
    unusable_rate = float(gen_cfg.get("unusable_file_rate", 0.02))

    n_messy = int(np.round(n_files * messy_rate))
    n_unusable = int(np.round(n_files * unusable_rate))

    # Pick indices for modifications
    shuffled_indices = rng.permutation(n_files)
    unusable_indices = set(shuffled_indices[:n_unusable])
    recoverable_messy_indices = set(shuffled_indices[n_unusable:n_messy])

    manifest: list[dict[str, Any]] = []

    # 1. Unusable defects (Quarantine expected)
    # Types: missing required column, severe corruption
    for idx in unusable_indices:
        file_path = files[idx]
        df = pd.read_csv(file_path)
        defect_type = "missing_column"

        # Drop a critical column
        cols = [c for c in df.columns if c != "date"]
        if cols:
            dropped_col = rng.choice(cols)
            df = df.drop(columns=[dropped_col])
            df.to_csv(file_path, index=False)
            manifest.append(
                {
                    "file": file_path.name,
                    "defect": "missing_column",
                    "dropped_column": dropped_col,
                    "quarantine_expected": True,
                }
            )

    # 2. Recoverable defects (Handled by normalization / cleaning / warnings)
    recoverable_types = [
        "french_excel",
        "date_dmy",
        "encoding_latin1",
        "encoding_utf8_bom",
        "duplicate_rows",
        "renamed_column",
        "unit_slip_mwh",
        "negative_value",
        "blank_lines_trailing_spaces",
        "late_correction",
    ]

    for i, idx in enumerate(recoverable_messy_indices):
        file_path = files[idx]
        defect_type = recoverable_types[i % len(recoverable_types)]
        record: dict[str, Any] = {
            "file": file_path.name,
            "defect": defect_type,
            "quarantine_expected": False,
        }

        if defect_type == "french_excel":
            # Semicolon separator, comma decimal
            df = pd.read_csv(file_path)
            content = df.to_csv(sep=";", decimal=",", index=False)
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)

        elif defect_type == "date_dmy":
            # Convert YYYY-MM-DD to DD/MM/YYYY
            df = pd.read_csv(file_path)
            df["date"] = pd.to_datetime(df["date"]).dt.strftime("%d/%m/%Y")
            df.to_csv(file_path, index=False)

        elif defect_type == "encoding_latin1":
            # Save file in latin-1 with accented characters
            df = pd.read_csv(file_path)
            # rename date column with french accent or add comment header
            content = df.to_csv(index=False)
            with open(file_path, "w", encoding="latin-1") as f:
                f.write("# Exportation Département Énergie / Production\n")
                f.write(content)

        elif defect_type == "encoding_utf8_bom":
            # Save with UTF-8 BOM
            content = file_path.read_text(encoding="utf-8")
            with open(file_path, "w", encoding="utf-8-sig") as f:
                f.write(content)

        elif defect_type == "duplicate_rows":
            # Duplicate the 5th and 12th row
            df = pd.read_csv(file_path)
            if len(df) > 15:
                dups = df.iloc[[4, 11]]
                df = pd.concat([df.iloc[:5], dups, df.iloc[5:]], ignore_index=True)
            df.to_csv(file_path, index=False)

        elif defect_type == "renamed_column":
            # Alias column
            df = pd.read_csv(file_path)
            rename_map = {
                "electricity_kwh": "Electricité (kWh)",
                "fuel_oil_l": "Fioul (L)",
                "units_produced": "Quantité Produite",
                "trips": "Nombre de trajets",
                "diesel_l": "Gazole (L)",
                "waste_kg": "Déchets (kg)",
                "water_m3": "Eau (m3)",
            }
            renamed = {}
            for k, v in rename_map.items():
                if k in df.columns:
                    renamed[k] = v
                    break
            if renamed:
                df = df.rename(columns=renamed)
                record["rename_applied"] = renamed
            df.to_csv(file_path, index=False)

        elif defect_type == "unit_slip_mwh":
            # Energy department reported MWh instead of kWh (1000x smaller)
            df = pd.read_csv(file_path)
            if "electricity_kwh" in df.columns:
                df["electricity_kwh"] = df["electricity_kwh"] / 1000.0
                record["unit_slip"] = "electricity_kwh / 1000"
            df.to_csv(file_path, index=False)

        elif defect_type == "negative_value":
            # Introduce a negative value in row 8
            df = pd.read_csv(file_path)
            numeric_cols = [c for c in df.columns if c not in ("date", "shutdown_flag")]
            if numeric_cols and len(df) > 8:
                col = numeric_cols[0]
                df.at[7, col] = -abs(df.at[7, col])
                record["negative_col"] = col
                record["negative_row"] = 7
            df.to_csv(file_path, index=False)

        elif defect_type == "blank_lines_trailing_spaces":
            # Insert trailing spaces and blank rows
            lines = file_path.read_text(encoding="utf-8").splitlines()
            new_lines = []
            for i, line in enumerate(lines):
                if i % 7 == 0:
                    new_lines.append(line + "   ")
                    new_lines.append("")  # blank row
                else:
                    new_lines.append(line)
            file_path.write_text("\n".join(new_lines), encoding="utf-8")

        elif defect_type == "late_correction":
            # Second file sent for same department & month with slightly different numbers
            df = pd.read_csv(file_path)
            numeric_cols = [c for c in df.columns if c not in ("date", "shutdown_flag")]
            if numeric_cols:
                df[numeric_cols[0]] = df[numeric_cols[0]] * 1.02  # 2% corrected revision
            corr_path = inbox_dir / f"{file_path.stem}_correction.csv"
            df.to_csv(corr_path, index=False)
            record["correction_file"] = corr_path.name

        manifest.append(record)

    manifest_file = root / "data" / "messy_manifest.json"
    with open(manifest_file, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    return {
        "total_files": n_files,
        "total_defects": len(manifest),
        "unusable_count": len(unusable_indices),
        "recoverable_count": len(recoverable_messy_indices),
        "manifest_path": str(manifest_file.relative_to(root)),
    }


if __name__ == "__main__":
    print("Messifying inbox files...")
    res = messify_inbox()
    print(
        f"Messify complete: {res['total_defects']} files modified "
        f"({res['unusable_count']} unusable, {res['recoverable_count']} recoverable)."
    )
