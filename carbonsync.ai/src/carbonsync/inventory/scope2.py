"""Scope 2 by both GHG Protocol methods (Scope 2 Guidance, 2015), as ESRS E1-6 asks.

* **Location-based**: every kWh bought from the grid × the grid average factor
  (``emission_factors.electricity_kgco2_per_kwh``). This is the daily ``co2e_scope2_t``.
* **Market-based**: the kWh covered by contractual instruments (PPA with its certificates, green tariff,
  energy attribute certificates) × the instrument's factor, plus the rest × the **residual mix** factor.

``scope2`` in config.yaml:

    residual_mix_kgco2_per_kwh: 0.483      # no published residual mix for Tunisia: grid average as proxy
    residual_mix_source: "..."
    instruments:
      - name: Green tariff (example)
        kind: green_tariff                  # ppa | green_tariff | certificates
        start: '2027-01-01'
        end: null                           # open-ended
        share_of_grid_kwh: 0.30             # or mwh_per_year: 1200
        kgco2_per_kwh: 0.0
        enabled: false

Electricity produced and used on site (the solar panels of 2021) never goes through the grid meter, so it is
in neither figure: the meter's kWh already fall by the solar share.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

KINDS = ("ppa", "green_tariff", "certificates")
KIND_LABELS = {"ppa": ("PPA (contrat d'achat direct)", "PPA (power purchase agreement)"),
               "green_tariff": ("Offre verte du fournisseur", "Supplier green tariff"),
               "certificates": ("Certificats (GO, I-REC)", "Certificates (GOs, I-RECs)")}
DEFAULT_SOURCE = ("No published residual mix for Tunisia: the national grid average is used as a proxy, as the "
                  "GHG Protocol Scope 2 Guidance allows when no residual mix exists. Market-based then equals "
                  "location-based until a contractual instrument is recorded.")


def settings(cfg: dict[str, Any] | None) -> dict[str, Any]:
    """Cleaned ``scope2`` section; the residual mix defaults to the grid factor."""
    cfg = cfg or {}
    raw = dict(cfg.get("scope2", {}) or {})
    grid = float(cfg.get("emission_factors", {}).get("electricity_kgco2_per_kwh", 0.483))
    rm = raw.get("residual_mix_kgco2_per_kwh")
    out = {"grid_kgco2_per_kwh": grid,
           "residual_mix_kgco2_per_kwh": grid if rm is None else max(0.0, float(rm)),
           "residual_mix_source": str(raw.get("residual_mix_source") or DEFAULT_SOURCE),
           "instruments": []}
    for i, inst in enumerate(raw.get("instruments") or []):
        if not isinstance(inst, dict):
            continue
        share = inst.get("share_of_grid_kwh")
        mwh = inst.get("mwh_per_year")
        out["instruments"].append({
            "name": str(inst.get("name") or f"Instrument {i + 1}"),
            "kind": inst.get("kind") if inst.get("kind") in KINDS else "certificates",
            "start": pd.Timestamp(inst["start"]) if inst.get("start") else None,
            "end": pd.Timestamp(inst["end"]) if inst.get("end") else None,
            "share_of_grid_kwh": None if share in (None, "") else min(1.0, max(0.0, float(share))),
            "mwh_per_year": None if mwh in (None, "") else max(0.0, float(mwh)),
            "kgco2_per_kwh": max(0.0, float(inst.get("kgco2_per_kwh") or 0.0)),
            "enabled": bool(inst.get("enabled", True)),
        })
    return out


def daily(df: pd.DataFrame, cfg: dict[str, Any] | None) -> pd.DataFrame:
    """Per day: grid kWh, kWh covered by instruments, Scope 2 location-based and market-based (t)."""
    s = settings(cfg)
    out = pd.DataFrame({"date": pd.to_datetime(df["date"]),
                        "electricity_kwh": pd.to_numeric(df["electricity_kwh"], errors="coerce").fillna(0.0)})
    grid_kwh = out["electricity_kwh"]
    covered = pd.Series(0.0, index=out.index)
    covered_t = pd.Series(0.0, index=out.index)
    for inst in s["instruments"]:
        if not inst["enabled"]:
            continue
        active = pd.Series(True, index=out.index)
        if inst["start"] is not None:
            active &= out["date"] >= inst["start"]
        if inst["end"] is not None:
            active &= out["date"] <= inst["end"]
        if inst["share_of_grid_kwh"] is not None:
            kwh = grid_kwh * inst["share_of_grid_kwh"]
        elif inst["mwh_per_year"] is not None:
            days = out["date"].dt.is_leap_year.map({True: 366, False: 365})
            kwh = inst["mwh_per_year"] * 1000.0 / days
        else:
            continue
        kwh = kwh.where(active, 0.0)
        covered = covered + kwh
        covered_t = covered_t + kwh * inst["kgco2_per_kwh"] / 1000.0
    # Instruments can never cover more than the electricity bought: scale them down if they would
    over = covered > grid_kwh
    if over.any():
        scale = (grid_kwh / covered.where(covered > 0, 1.0)).where(over, 1.0)
        covered = covered * scale
        covered_t = covered_t * scale
    out["covered_kwh"] = covered
    out["scope2_location_t"] = grid_kwh * s["grid_kgco2_per_kwh"] / 1000.0
    out["scope2_market_t"] = covered_t + (grid_kwh - covered) * s["residual_mix_kgco2_per_kwh"] / 1000.0
    return out


def totals(df: pd.DataFrame, cfg: dict[str, Any] | None, start: Any = None, end: Any = None) -> dict[str, Any]:
    """Both Scope 2 figures over a period (default: everything in ``df``)."""
    d = df
    if start is not None:
        d = d[pd.to_datetime(d["date"]) >= pd.Timestamp(start)]
    if end is not None:
        d = d[pd.to_datetime(d["date"]) <= pd.Timestamp(end)]
    s = settings(cfg)
    if d.empty:
        return {"location_t": 0.0, "market_t": 0.0, "grid_kwh": 0.0, "covered_kwh": 0.0, "covered_pct": 0.0,
                "residual_mix_kgco2_per_kwh": s["residual_mix_kgco2_per_kwh"],
                "grid_kgco2_per_kwh": s["grid_kgco2_per_kwh"], "instruments_active": 0}
    x = daily(d, cfg)
    grid = float(x["electricity_kwh"].sum())
    cov = float(x["covered_kwh"].sum())
    return {"location_t": float(x["scope2_location_t"].sum()), "market_t": float(x["scope2_market_t"].sum()),
            "grid_kwh": grid, "covered_kwh": cov, "covered_pct": (cov / grid * 100) if grid else 0.0,
            "residual_mix_kgco2_per_kwh": s["residual_mix_kgco2_per_kwh"],
            "grid_kgco2_per_kwh": s["grid_kgco2_per_kwh"],
            "instruments_active": sum(1 for i in s["instruments"] if i["enabled"]),
            "residual_mix_is_grid_proxy": abs(s["residual_mix_kgco2_per_kwh"] - s["grid_kgco2_per_kwh"]) < 1e-9}


def note(t: dict[str, Any], lang: str = "fr") -> str:
    """Why the two figures differ (or not), in one sentence."""
    fr = lang == "fr"
    if t.get("covered_kwh", 0) <= 0 and t.get("residual_mix_is_grid_proxy", True):
        return ("Les deux méthodes donnent le même chiffre : aucun instrument contractuel enregistré, et pas de "
                "mix résiduel publié pour la Tunisie (moyenne du réseau utilisée)." if fr else
                "Both methods give the same figure: no contractual instrument is recorded, and Tunisia has no "
                "published residual mix (grid average used).")
    pct = t.get("covered_pct", 0.0)
    return (f"{pct:.0f} % de l'électricité achetée est couverte par des instruments contractuels ; le reste est "
            f"compté au mix résiduel." if fr else
            f"{pct:.0f}% of the electricity bought is covered by contractual instruments; the rest is counted at "
            f"the residual mix.")
