"""Emissions by plant zone and item, from allocation keys, so each reduction action has an owner.

The departments send one meter per energy carrier (all the site's kWh, all its fuel oil…). Until sub-meters
exist, **allocation keys** split each carrier between items (cutting machines, compressed air, generators,
hazardous waste…) and each item belongs to a zone (P1 cutting, P2 preparation, P3 assembly, utilities,
logistics) with an owner. ``breakdown`` in config.yaml:

    zones:
      P1: {fr: P1 Coupe, en: P1 Cutting, owner: Responsable P1}
    keys:
      electricity_kwh:
        - {fr: Machines de coupe, en: Cutting machines, zone: P1, share: 0.18}

The shares of each carrier must total 1, so the breakdown always adds up to the site total (Scope 1 +
Scope 2 location-based + waste & water). The keys are estimates: the page says so, and they should be
replaced by sub-meter readings when available.
"""
from __future__ import annotations

import copy
from typing import Any

import pandas as pd

SOURCES: dict[str, dict[str, Any]] = {
    "electricity_kwh": {"factor": "electricity_kgco2_per_kwh", "scope": "scope2",
                        "fr": "Électricité", "en": "Electricity"},
    "fuel_oil_l": {"factor": "fuel_oil_kgco2_per_l", "scope": "scope1", "fr": "Fioul lourd", "en": "Heavy fuel oil"},
    "diesel_l": {"factor": "diesel_kgco2_per_l", "scope": "scope1", "fr": "Gazole", "en": "Diesel"},
    "waste_kg": {"factor": "waste_kgco2_per_kg", "scope": "other", "fr": "Déchets", "en": "Waste"},
    "water_m3": {"factor": "water_kgco2_per_m3", "scope": "other", "fr": "Eau", "en": "Water"},
}

DEFAULTS: dict[str, Any] = {
    "zones": {
        "P1": {"fr": "P1 Coupe", "en": "P1 Cutting", "owner": "Responsable P1"},
        "P2": {"fr": "P2 Préparation", "en": "P2 Preparation", "owner": "Responsable P2"},
        "P3": {"fr": "P3 Assemblage", "en": "P3 Assembly", "owner": "Responsable P3"},
        "UT": {"fr": "Utilités et bâtiments", "en": "Utilities and buildings", "owner": "Maintenance"},
        "LOG": {"fr": "Logistique", "en": "Logistics", "owner": "Responsable logistique"},
    },
    "keys": {
        "electricity_kwh": [
            {"fr": "Machines de coupe", "en": "Cutting machines", "zone": "P1", "share": 0.18},
            {"fr": "Sertissage et préparation", "en": "Crimping and preparation", "zone": "P2", "share": 0.14},
            {"fr": "Lignes d'assemblage et bancs de test", "en": "Assembly lines and test benches", "zone": "P3",
             "share": 0.26},
            {"fr": "Air comprimé", "en": "Compressed air", "zone": "UT", "share": 0.12},
            {"fr": "Climatisation et ventilation", "en": "Air conditioning and ventilation", "zone": "UT",
             "share": 0.18},
            {"fr": "Éclairage et bureaux", "en": "Lighting and offices", "zone": "UT", "share": 0.08},
            {"fr": "Entrepôt et quais", "en": "Warehouse and docks", "zone": "LOG", "share": 0.04},
        ],
        "fuel_oil_l": [
            {"fr": "Groupes électrogènes", "en": "Generators", "zone": "UT", "share": 0.35},
            {"fr": "Chaudière (chauffage, eau chaude)", "en": "Boiler (heating, hot water)", "zone": "UT",
             "share": 0.65},
        ],
        "diesel_l": [
            {"fr": "Camions de livraison", "en": "Delivery trucks", "zone": "LOG", "share": 0.75},
            {"fr": "Chariots élévateurs et navettes", "en": "Forklifts and site shuttles", "zone": "LOG",
             "share": 0.25},
        ],
        "waste_kg": [
            {"fr": "Chutes de cuivre et de câbles", "en": "Copper and cable scrap", "zone": "P1", "share": 0.35},
            {"fr": "Chutes de PVC et de gaines", "en": "PVC and sleeve scrap", "zone": "P2", "share": 0.20},
            {"fr": "Emballages", "en": "Packaging", "zone": "P3", "share": 0.30},
            {"fr": "Déchets dangereux", "en": "Hazardous waste", "zone": "UT", "share": 0.05},
            {"fr": "Déchets ménagers et cantine", "en": "General and canteen waste", "zone": "UT", "share": 0.10},
        ],
        "water_m3": [
            {"fr": "Sanitaires et cantine", "en": "Sanitary and canteen", "zone": "UT", "share": 0.70},
            {"fr": "Refroidissement et process", "en": "Cooling and process", "zone": "UT", "share": 0.30},
        ],
    },
}


def settings(cfg: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict((cfg or {}).get("breakdown", {}) or {})
    out = copy.deepcopy(DEFAULTS)
    if isinstance(raw.get("zones"), dict) and raw["zones"]:
        out["zones"] = {str(k): dict(v or {}) for k, v in raw["zones"].items()}
    if isinstance(raw.get("keys"), dict):
        for src, items in raw["keys"].items():
            if src in SOURCES and isinstance(items, list):
                out["keys"][src] = [dict(i) for i in items if isinstance(i, dict)]
    return out


def validate(s: dict[str, Any]) -> list[str]:
    """Problems that would make the breakdown not add up (empty list = fine)."""
    errors = []
    for src, items in s["keys"].items():
        total = sum(float(i.get("share", 0) or 0) for i in items)
        if abs(total - 1.0) > 0.005:
            errors.append(f"{src}: shares total {total:.3f}, not 1")
        for i in items:
            if i.get("zone") not in s["zones"]:
                errors.append(f"{src}: item '{i.get('en') or i.get('fr')}' has an unknown zone '{i.get('zone')}'")
    for src in SOURCES:
        if not s["keys"].get(src):
            errors.append(f"{src}: no allocation key")
    return errors


def allocate(df: pd.DataFrame, cfg: dict[str, Any] | None, start: Any = None, end: Any = None,
             freq: str | None = None) -> pd.DataFrame:
    """tCO2e per zone and item over a period; with ``freq`` ("M"), one block of rows per month."""
    s = settings(cfg)
    ef = (cfg or {}).get("emission_factors", {})
    d = df.copy()
    d["date"] = pd.to_datetime(d["date"])
    if start is not None:
        d = d[d["date"] >= pd.Timestamp(start)]
    if end is not None:
        d = d[d["date"] <= pd.Timestamp(end)]
    if d.empty:
        return pd.DataFrame(columns=["period", "zone", "zone_fr", "zone_en", "owner", "item_fr", "item_en",
                                     "source", "source_fr", "source_en", "scope", "t"])
    groups = [("all", d)] if not freq else [(str(k.date()), g) for k, g in
                                             d.groupby(d["date"].dt.to_period(freq).dt.start_time)]
    rows = []
    for period, g in groups:
        for src, meta in SOURCES.items():
            if src not in g:
                continue
            src_t = float(pd.to_numeric(g[src], errors="coerce").fillna(0).sum()) * float(ef.get(meta["factor"], 0)) / 1000
            for item in s["keys"].get(src, []):
                z = s["zones"].get(item.get("zone"), {})
                rows.append({"period": period, "zone": item.get("zone"), "zone_fr": z.get("fr", item.get("zone")),
                             "zone_en": z.get("en", item.get("zone")), "owner": z.get("owner", ""),
                             "item_fr": item.get("fr", item.get("en", "")), "item_en": item.get("en", item.get("fr", "")),
                             "source": src, "source_fr": meta["fr"], "source_en": meta["en"], "scope": meta["scope"],
                             "t": src_t * float(item.get("share", 0) or 0)})
    return pd.DataFrame(rows)


def by_zone(df: pd.DataFrame, cfg: dict[str, Any] | None, start: Any = None, end: Any = None) -> pd.DataFrame:
    a = allocate(df, cfg, start, end)
    if a.empty:
        return pd.DataFrame(columns=["zone", "zone_fr", "zone_en", "owner", "t", "share_pct"])
    z = a.groupby(["zone", "zone_fr", "zone_en", "owner"], dropna=False)["t"].sum().reset_index()
    z["share_pct"] = z["t"] / z["t"].sum() * 100 if z["t"].sum() else 0.0
    return z.sort_values("t", ascending=False).reset_index(drop=True)


def by_item(df: pd.DataFrame, cfg: dict[str, Any] | None, start: Any = None, end: Any = None) -> pd.DataFrame:
    a = allocate(df, cfg, start, end)
    if a.empty:
        return a
    i = a.groupby(["zone", "zone_fr", "zone_en", "owner", "item_fr", "item_en", "source", "source_fr",
                   "source_en", "scope"], dropna=False)["t"].sum().reset_index()
    i["share_pct"] = i["t"] / i["t"].sum() * 100 if i["t"].sum() else 0.0
    return i.sort_values("t", ascending=False).reset_index(drop=True)
