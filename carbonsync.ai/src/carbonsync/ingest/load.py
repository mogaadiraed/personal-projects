"""
CarbonSync AI – DuckDB Ingestion Loader
=========================================
Loads validated departmental Parquet files from data/processed/ into DuckDB:
  - raw_energy
  - raw_production
  - raw_logistics
  - raw_waste_water

Each raw table includes audit tracking columns:
  - source_file (VARCHAR)
  - loaded_at (TIMESTAMP)

Handles late corrections / duplicate updates by keeping the latest loaded record per date.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import yaml

from carbonsync.obs import get_logger

log = get_logger(__name__)


def get_project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def load_config(config_path: Path | None = None) -> dict[str, Any]:
    if config_path is None:
        config_path = get_project_root() / "config.yaml"
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_duckdb_path(config: dict[str, Any] | None = None) -> Path:
    root = get_project_root()
    if config is None:
        config = load_config()
    db_rel = config.get("paths", {}).get("duckdb", "data/carbonsync.duckdb")
    db_path = root / db_rel
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return db_path


def load_processed_to_duckdb(config_path: Path | None = None) -> dict[str, Any]:
    """Loads all processed parquet files into their respective DuckDB raw tables."""
    root = get_project_root()
    cfg = load_config(config_path)
    processed_dir = root / cfg.get("paths", {}).get("processed", "data/processed")
    db_path = get_duckdb_path(cfg)

    # Connect to DuckDB database file
    conn = duckdb.connect(str(db_path))

    dept_table_map = {
        "energie": "raw_energy",
        "production": "raw_production",
        "logistique": "raw_logistics",
        "dechets_eau": "raw_waste_water",
    }

    results: dict[str, int] = {}
    now_ts = datetime.now(UTC)

    for dept, table_name in dept_table_map.items():
        dept_dir = processed_dir / dept
        if not dept_dir.exists():
            continue

        parquet_files = sorted(dept_dir.glob("*.parquet"))
        if not parquet_files:
            continue

        dfs: list[pd.DataFrame] = []
        for pf in parquet_files:
            df = pd.read_parquet(pf)
            df["source_file"] = pf.name
            df["loaded_at"] = now_ts
            dfs.append(df)

        combined_df = pd.concat(dfs, ignore_index=True)

        # In case of duplicate dates across monthly/correction files, keep the latest by date & source_file
        combined_df = combined_df.sort_values(by=["date", "source_file"]).drop_duplicates(
            subset=["date"], keep="last"
        )

        # Create or replace DuckDB table using cursor
        cursor = conn.cursor()
        cursor.register("temp_df", combined_df)
        cursor.execute(f"CREATE OR REPLACE TABLE {table_name} AS SELECT * FROM temp_df ORDER BY date")
        cursor.unregister("temp_df")
        row_count = cursor.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
        cursor.close()
        results[table_name] = int(row_count)
        log.info("DuckDB %-16s ← %3d files · %5d days · %s → %s", table_name, len(parquet_files), row_count,
                 combined_df["date"].min().date(), combined_df["date"].max().date())

    conn.close()

    return {
        "database": str(db_path.relative_to(root)),
        "tables_loaded": results,
    }


if __name__ == "__main__":
    print("Loading processed data into DuckDB...")
    res = load_processed_to_duckdb()
    print(f"Loaded into {res['database']}: {res['tables_loaded']}")
