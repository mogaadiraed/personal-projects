"""Scope 3 screening: the 15 GHG Protocol categories, each calculated, estimated or excluded.

A screening gives the order of magnitude of each category so the material ones get measured first (GHG
Protocol Scope 3 Standard, ch. 6; ESRS E1-6 asks for significant Scope 3 categories). Three statuses:

* **calculated**: from the site's own metered activity data (waste, water);
* **estimated**: from drivers the dashboard already has (units produced, fuels, kWh, working days) times
  stated assumptions and published factors. Every assumption sits in ``scope3`` in config.yaml with its
  source; replace it with purchase records or supplier data when they exist;
* **excluded**: with the reason (not applicable, or expected small and not yet measured).

The default assumptions describe a wiring-harness plant: copper and PVC bought per unit produced, inbound
materials by sea then road, finished harnesses shipped to customers, staff commuting mostly by company bus.
They are screening values (low data quality), not measured figures.
"""
from __future__ import annotations

import copy
from typing import Any

import pandas as pd

CATEGORIES: dict[int, tuple[str, str]] = {
    1: ("Biens et services achetés", "Purchased goods and services"),
    2: ("Biens d'équipement", "Capital goods"),
    3: ("Activités liées aux combustibles et à l'énergie", "Fuel- and energy-related activities"),
    4: ("Transport et distribution amont", "Upstream transportation and distribution"),
    5: ("Déchets générés par les opérations", "Waste generated in operations"),
    6: ("Déplacements professionnels", "Business travel"),
    7: ("Trajets domicile-travail", "Employee commuting"),
    8: ("Actifs loués en amont", "Upstream leased assets"),
    9: ("Transport et distribution aval", "Downstream transportation and distribution"),
    10: ("Transformation des produits vendus", "Processing of sold products"),
    11: ("Utilisation des produits vendus", "Use of sold products"),
    12: ("Fin de vie des produits vendus", "End-of-life treatment of sold products"),
    13: ("Actifs loués en aval", "Downstream leased assets"),
    14: ("Franchises", "Franchises"),
    15: ("Investissements", "Investments"),
}
STATUS_LABELS = {"calculated": ("Calculé", "Calculated"), "estimated": ("Estimé", "Estimated"),
                 "excluded": ("Exclu", "Excluded")}

DEFAULTS: dict[str, Any] = {
    "purchased_materials": [
        {"name_fr": "Cuivre (fils, bornes)", "name_en": "Copper (wire, terminals)", "kg_per_unit": 0.5,
         "kgco2_per_kg": 4.0,
         "source": "Order of magnitude of cradle-to-gate refined copper (ICA life-cycle inventory, global "
                   "average); replace with supplier product carbon footprints"},
        {"name_fr": "PVC (isolants, gaines)", "name_en": "PVC (insulation, sleeves)", "kg_per_unit": 0.3,
         "kgco2_per_kg": 2.08,
         "source": "PlasticsEurope / ECVM eco-profile, suspension PVC, cradle-to-gate (2026 revision)"},
    ],
    "fuel_energy": {
        "fuels_wtt_kgco2_per_l": 0.61,
        "electricity_upstream_kgco2_per_kwh": 0.07,
        "source": "Well-to-tank of fuel oil and diesel (UK DESNZ conversion factors, order of magnitude) and "
                  "grid losses + upstream of generation (about 15 % of the grid factor)",
    },
    "transport_inbound": {
        "legs": [{"mode": "sea", "km": 2500, "kgco2_per_tkm": 0.016},
                 {"mode": "road", "km": 500, "kgco2_per_tkm": 0.10}],
        "source": "Purchased materials (mass above) by container ship then truck; DESNZ freight factors, "
                  "order of magnitude",
    },
    "transport_outbound": {
        "kg_per_unit": 0.9,
        "legs": [{"mode": "road", "km": 500, "kgco2_per_tkm": 0.10},
                 {"mode": "sea", "km": 2500, "kgco2_per_tkm": 0.016}],
        "source": "Finished harness with packaging, shipped to vehicle plants; DESNZ freight factors, order of "
                  "magnitude",
    },
    "commuting": {
        "employees": 1000,
        "round_trip_km": 30,
        "attendance": 0.95,
        "modes": [{"mode_fr": "Bus de l'entreprise", "mode_en": "Company bus", "share": 0.7, "kgco2_per_pkm": 0.03},
                  {"mode_fr": "Voiture", "mode_en": "Car", "share": 0.2, "kgco2_per_pkm": 0.17},
                  {"mode_fr": "Taxi collectif, autre", "mode_en": "Shared taxi, other", "share": 0.1,
                   "kgco2_per_pkm": 0.10}],
        "source": "Head count, distance and mode split are assumptions to replace with an HR survey; "
                  "per-passenger factors are DESNZ orders of magnitude",
    },
    "excluded": {
        2: "No capital-expenditure data yet; estimate from the asset ledger (spend-based) next year",
        6: "No travel records; expected small for a production site",
        8: "None: leased buildings and equipment are already in Scopes 1 and 2",
        10: "Harnesses are fitted into vehicles by the customer; no data, usually small for this product",
        11: "A harness uses no energy itself (indirect use-phase emissions are optional)",
        12: "Recycled with the vehicle; expected small, not yet estimated",
        13: "None", 14: "None", 15: "None",
    },
}


def settings(cfg: dict[str, Any] | None) -> dict[str, Any]:
    """``scope3`` from config.yaml, each block falling back to the defaults above."""
    raw = dict((cfg or {}).get("scope3", {}) or {})
    out = copy.deepcopy(DEFAULTS)
    for key in ("purchased_materials",):
        if isinstance(raw.get(key), list):
            out[key] = [dict(m) for m in raw[key] if isinstance(m, dict)]
    for key in ("fuel_energy", "transport_inbound", "transport_outbound", "commuting"):
        if isinstance(raw.get(key), dict):
            out[key] = {**out[key], **raw[key]}
    if isinstance(raw.get("excluded"), dict):
        out["excluded"] = {int(k): str(v) for k, v in raw["excluded"].items()}
    return out


def _period(df: pd.DataFrame, start: Any, end: Any) -> pd.DataFrame:
    d = df.copy()
    d["date"] = pd.to_datetime(d["date"])
    if start is not None:
        d = d[d["date"] >= pd.Timestamp(start)]
    if end is not None:
        d = d[d["date"] <= pd.Timestamp(end)]
    return d


def _num(d: pd.DataFrame, col: str) -> float:
    return float(pd.to_numeric(d[col], errors="coerce").fillna(0).sum()) if col in d else 0.0


def _legs_factor(legs: list[dict[str, Any]]) -> float:
    """kg CO2e per tonne carried over all legs."""
    return sum(float(leg.get("km", 0)) * float(leg.get("kgco2_per_tkm", 0)) for leg in legs or [])


def screening(df: pd.DataFrame, cfg: dict[str, Any] | None, start: Any = None, end: Any = None) -> pd.DataFrame:
    """One row per category item: category, names, status, method, tCO2e (None when excluded), quality, source."""
    s = settings(cfg)
    ef = (cfg or {}).get("emission_factors", {})
    d = _period(df, start, end)
    units = _num(d, "units_produced")
    weekdays = float((1 - pd.to_numeric(d.get("is_weekend", 0), errors="coerce").fillna(0)).sum()) if not d.empty else 0.0
    rows: list[dict[str, Any]] = []

    def add(cat: int, item_fr: str, item_en: str, status: str, method_fr: str, method_en: str,
            t: float | None, quality: str, source: str) -> None:
        rows.append({"category": cat, "category_fr": CATEGORIES[cat][0], "category_en": CATEGORIES[cat][1],
                     "item_fr": item_fr, "item_en": item_en, "status": status, "method_fr": method_fr,
                     "method_en": method_en, "t": None if t is None else round(float(t), 3),
                     "quality": quality, "source": source})

    # 1 · purchased materials (estimated) and water supply (calculated from the meter)
    mat_kg_unit = 0.0
    for m in s["purchased_materials"]:
        kg = float(m.get("kg_per_unit", 0))
        mat_kg_unit += kg
        t = units * kg * float(m.get("kgco2_per_kg", 0)) / 1000.0
        add(1, str(m.get("name_fr", m.get("name_en", "Matière"))), str(m.get("name_en", m.get("name_fr", "Material"))),
            "estimated", f"{kg:g} kg/unité × {float(m.get('kgco2_per_kg', 0)):g} kgCO₂e/kg × unités produites",
            f"{kg:g} kg/unit × {float(m.get('kgco2_per_kg', 0)):g} kgCO₂e/kg × units produced", t, "low",
            str(m.get("source", "")))
    water_f = float(ef.get("water_kgco2_per_m3", 0.3))
    add(1, "Eau (fourniture et traitement)", "Water (supply and treatment)", "calculated",
        f"m³ relevés × {water_f:g} kgCO₂e/m³", f"metered m³ × {water_f:g} kgCO₂e/m³",
        _num(d, "water_m3") * water_f / 1000.0, "high", "Site meter readings; factor in config.yaml")

    # 3 · upstream of fuels and electricity (estimated)
    fe = s["fuel_energy"]
    litres = _num(d, "fuel_oil_l") + _num(d, "diesel_l")
    kwh = _num(d, "electricity_kwh")
    add(3, "Amont des combustibles (puits au réservoir)", "Upstream of fuels (well-to-tank)", "estimated",
        f"litres de fioul et gazole × {float(fe['fuels_wtt_kgco2_per_l']):g} kgCO₂e/L",
        f"fuel oil and diesel litres × {float(fe['fuels_wtt_kgco2_per_l']):g} kgCO₂e/L",
        litres * float(fe["fuels_wtt_kgco2_per_l"]) / 1000.0, "medium", str(fe.get("source", "")))
    add(3, "Pertes réseau et amont de l'électricité", "Grid losses and upstream of electricity", "estimated",
        f"kWh achetés × {float(fe['electricity_upstream_kgco2_per_kwh']):g} kgCO₂e/kWh",
        f"kWh bought × {float(fe['electricity_upstream_kgco2_per_kwh']):g} kgCO₂e/kWh",
        kwh * float(fe["electricity_upstream_kgco2_per_kwh"]) / 1000.0, "medium", str(fe.get("source", "")))

    # 4 · inbound transport of the purchased materials (estimated)
    tin = s["transport_inbound"]
    f_in = _legs_factor(tin.get("legs", []))
    km_in = sum(float(x.get("km", 0)) for x in tin.get("legs", []))
    add(4, "Transport des matières achetées", "Transport of purchased materials", "estimated",
        f"{mat_kg_unit:g} kg/unité × unités × {km_in:,.0f} km (mer + route)",
        f"{mat_kg_unit:g} kg/unit × units × {km_in:,.0f} km (sea + road)",
        units * mat_kg_unit / 1000.0 * f_in / 1000.0, "low", str(tin.get("source", "")))

    # 5 · waste (calculated from the site's weighings)
    waste_f = float(ef.get("waste_kgco2_per_kg", 0.5))
    add(5, "Déchets (tous flux)", "Waste (all streams)", "calculated",
        f"kg pesés × {waste_f:g} kgCO₂e/kg", f"weighed kg × {waste_f:g} kgCO₂e/kg",
        _num(d, "waste_kg") * waste_f / 1000.0, "high", "Site weighings; factor in config.yaml")

    # 7 · commuting (estimated)
    c = s["commuting"]
    modes = c.get("modes", []) or []
    share = sum(float(m.get("share", 0)) for m in modes) or 1.0
    f_pkm = sum(float(m.get("share", 0)) * float(m.get("kgco2_per_pkm", 0)) for m in modes) / share
    pkm = float(c["employees"]) * float(c["round_trip_km"]) * float(c.get("attendance", 1.0)) * weekdays
    add(7, "Trajets du personnel", "Staff commuting", "estimated",
        f"{int(c['employees'])} salariés × {float(c['round_trip_km']):g} km/jour × jours ouvrés × {f_pkm:.3f} kgCO₂e/km",
        f"{int(c['employees'])} staff × {float(c['round_trip_km']):g} km/day × working days × {f_pkm:.3f} kgCO₂e/km",
        pkm * f_pkm / 1000.0, "low", str(c.get("source", "")))

    # 9 · outbound transport of finished products (estimated)
    tout = s["transport_outbound"]
    f_out = _legs_factor(tout.get("legs", []))
    km_out = sum(float(x.get("km", 0)) for x in tout.get("legs", []))
    add(9, "Livraison des faisceaux aux clients", "Delivery of harnesses to customers", "estimated",
        f"{float(tout['kg_per_unit']):g} kg/unité × unités × {km_out:,.0f} km",
        f"{float(tout['kg_per_unit']):g} kg/unit × units × {km_out:,.0f} km",
        units * float(tout["kg_per_unit"]) / 1000.0 * f_out / 1000.0, "low", str(tout.get("source", "")))

    for cat, reason in sorted(s["excluded"].items()):
        if cat in {r["category"] for r in rows}:
            continue
        add(cat, "—", "—", "excluded", "Exclu de la présélection", "Excluded from the screening", None, "—", reason)
    out = pd.DataFrame(rows).sort_values(["category", "status"]).reset_index(drop=True)
    total = float(out["t"].fillna(0).sum())
    out["share_pct"] = out["t"].apply(lambda v: None if v is None or pd.isna(v) or not total else v / total * 100)
    return out


def summary(df: pd.DataFrame, cfg: dict[str, Any] | None, start: Any = None, end: Any = None) -> dict[str, Any]:
    """Totals by status and by category, plus the rows (JSON-friendly)."""
    rows = screening(df, cfg, start, end)
    by_cat = (rows.dropna(subset=["t"]).groupby(["category", "category_en", "category_fr"])["t"].sum()
              .reset_index().sort_values("t", ascending=False))
    by_status = rows.dropna(subset=["t"]).groupby("status")["t"].sum().to_dict()
    return {"total_t": float(rows["t"].fillna(0).sum()),
            "calculated_t": float(by_status.get("calculated", 0.0)),
            "estimated_t": float(by_status.get("estimated", 0.0)),
            "excluded_categories": sorted(int(c) for c in rows.loc[rows["status"] == "excluded", "category"]),
            "by_category": by_cat.to_dict(orient="records"),
            "rows": rows.where(pd.notna(rows), None).to_dict(orient="records")}
