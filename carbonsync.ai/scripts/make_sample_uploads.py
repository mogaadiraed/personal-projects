"""Generate ready-to-upload department CSVs in ``samples/`` that continue the dataset into 2026.

The same physics-based generator (same seed and parameters) is run over an
extended period, and only the new months are written out:

    samples/2026-Q1.zip            12 clean files (Jan–Mar 2026, 4 departments) in one archive
    samples/2026-Q2.zip            next quarter: 12 clean files (Apr–Jun 2026)
    samples/2026-Q3.zip            the quarter after: 12 clean files (Jul–Sep 2026)
    samples/2026-04_mixed/         April: French-format energy file (auto-repaired),
                                   two clean files, one production file with a missing column
    samples/2026-04_correction/    the corrected production file for April

Usage:
    python scripts/make_sample_uploads.py
"""
from __future__ import annotations

import shutil
import sys
import zipfile
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from carbonsync.synth.generator import generate_synthetic_data

DEPTS = ("energie", "production", "logistique", "dechets_eau")
OUT = ROOT / "samples"
TMP = ROOT / "data" / "_sample_tmp"


def _generate(end_date: str, tag: str) -> Path:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    cfg["generator"]["end_date"] = end_date
    cfg["paths"]["inbox"] = str((TMP / tag / "inbox").relative_to(ROOT)).replace("\\", "/")
    cfg["paths"]["truth"] = str((TMP / "truth").relative_to(ROOT)).replace("\\", "/")
    TMP.mkdir(parents=True, exist_ok=True)
    cfg_path = TMP / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")

    meta = ROOT / "data" / "synth_meta.json"
    saved_meta = meta.read_bytes() if meta.exists() else None
    try:
        generate_synthetic_data(config_path=cfg_path)
    finally:  # the generator also writes data/synth_meta.json – keep the real one
        if saved_meta is not None:
            meta.write_bytes(saved_meta)
    return TMP / tag / "inbox"


def _clean(path: Path) -> pd.DataFrame:
    """Department exports for the samples are complete: fill the generator's random gaps."""
    df = pd.read_csv(path)
    num = [c for c in df.columns if c != "date"]
    df[num] = df[num].interpolate(limit_direction="both")
    for c in ("trips", "shutdown_flag"):
        if c in df:
            df[c] = df[c].round().astype(int)
    return df


def main() -> None:
    # The generator's noise depends on the series length, so each pack keeps the run it was first made
    # with: Q1 / April from a run ending 2026-04-30 (files already uploaded stay identical),
    # Q2 from a run ending 2026-06-30, Q3 from a run ending 2026-09-30.
    inbox = _generate("2026-04-30", "a")
    inbox_q2 = _generate("2026-06-30", "b")
    inbox_q3 = _generate("2026-09-30", "c")
    if OUT.exists():
        shutil.rmtree(OUT)

    # --- Quarters: clean files, one per department and month ------------------
    for quarter, months, src in (("2026-Q1", ("2026-01", "2026-02", "2026-03"), inbox),
                                 ("2026-Q2", ("2026-04", "2026-05", "2026-06"), inbox_q2),
                                 ("2026-Q3", ("2026-07", "2026-08", "2026-09"), inbox_q3)):
        folder = OUT / quarter
        folder.mkdir(parents=True)
        for month in months:
            for d in DEPTS:
                _clean(src / f"{d}_{month}.csv").to_csv(folder / f"{d}_{month}.csv", index=False)
        with zipfile.ZipFile(OUT / f"{quarter}.zip", "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sorted(folder.glob("*.csv")):
                zf.write(f, f.name)
        shutil.rmtree(folder)   # the archive is the sample (Data intake reads .zip uploads)

    # --- April 2026: realistic mix ------------------------------------------
    apr = OUT / "2026-04_mixed"
    apr.mkdir()
    energy = _clean(inbox / "energie_2026-04.csv")
    french = energy.rename(columns={"electricity_kwh": "Electricité (kWh)", "fuel_oil_l": "Fioul (L)"})
    french["date"] = pd.to_datetime(french["date"]).dt.strftime("%d/%m/%Y")
    lines = ["# Export service Énergie – avril 2026", ";".join(french.columns)]
    for row in french.itertuples(index=False):
        lines.append(";".join([row[0]] + [f"{v:.2f}".replace(".", ",") for v in row[1:]]))
    (apr / "energie_2026-04.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")

    for d in ("logistique", "dechets_eau"):
        _clean(inbox / f"{d}_2026-04.csv").to_csv(apr / f"{d}_2026-04.csv", index=False)

    production = _clean(inbox / "production_2026-04.csv")
    production.drop(columns=["operating_hours"]).to_csv(apr / "production_2026-04.csv", index=False)

    fix = OUT / "2026-04_correction"
    fix.mkdir()
    production.to_csv(fix / "production_2026-04.csv", index=False)

    shutil.rmtree(TMP)
    files = sorted(p.relative_to(ROOT) for p in OUT.rglob("*") if p.is_file())
    print(f"Wrote {len(files)} files to {OUT.relative_to(ROOT)}/")
    for f in files:
        print("  ", f)


if __name__ == "__main__":
    main()
