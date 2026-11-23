"""The Analyse engine: deterministic carbon analysis the assistant explains but never computes.

Steps (each one a function, all pure pandas):

1. ``hotspots``      last N months vs the same months last year, vs the 12-month average,
                     and the seasonal rank against every earlier year.
2. ``drivers``       activity data behind it (kWh, litres, units, trips) and intensities.
3. ``decompose``     LMDI-I: splits the change of each source into a volume effect
                     (production / trips) and an intensity effect (consumption per unit).
4. ``project_year``  actual + forecast + a labelled seasonal estimate beyond the horizon,
                     against the annual cap, with the 90 % range.
5. ``reductions``    the gap turned into concrete targets (kWh/day, L/day, % vs now) for a
                     short window and for the rest of the year, with a feasibility label.
6. ``levers``        what-if levers (same simulator as the Scenarios page) ranked by how
                     much of the gap they close, plus the minimum single-lever effort.

``analyse()`` runs everything and writes the findings and the action plan in FR or EN.
Run ``python -m carbonsync.assistant`` to print it in the terminal.
"""
from __future__ import annotations

import math
import re
from typing import Any

import numpy as np
import pandas as pd

from carbonsync import targets
from carbonsync.assistant.context import DataContext, load_context
from carbonsync.export.insights import Fmt
from carbonsync.obs import get_logger, step

log = get_logger(__name__)

SCOPE_COLS = {"total": "co2e_total_t", "s2": "co2e_scope2_t", "s1": "co2e_scope1_t", "other": "co2e_other_t"}
SCOPE_NAMES = {"total": ("Émissions totales", "Total emissions"), "s2": ("Scope 2", "Scope 2"),
               "s1": ("Scope 1", "Scope 1"), "other": ("Autres émissions", "Other emissions")}

# source -> (activity data column, activity column, emission-factor key, scope, FR/EN label, unit)
SOURCES: dict[str, tuple[str, str, str, str, tuple[str, str], str]] = {
    "electricity": ("electricity_kwh", "units_produced", "electricity_kgco2_per_kwh", "s2",
                    ("Électricité réseau", "Grid electricity"), "kWh"),
    "fuel_oil": ("fuel_oil_l", "units_produced", "fuel_oil_kgco2_per_l", "s1", ("Fioul", "Fuel oil"), "L"),
    "diesel": ("diesel_l", "trips", "diesel_kgco2_per_l", "s1", ("Gazole logistique", "Logistics diesel"), "L"),
    "waste": ("waste_kg", "units_produced", "waste_kgco2_per_kg", "other", ("Déchets", "Waste"), "kg"),
    "water": ("water_m3", "units_produced", "water_kgco2_per_m3", "other", ("Eau", "Water"), "m³"),
}
ACTIVITY_NAMES = {"units_produced": ("unités produites", "units produced"), "trips": ("trajets", "trips")}

REALISTIC_PCT, AMBITIOUS_PCT = 10.0, 20.0
TREND_CLIP = (0.8, 1.2)
TREND_MONTHS = 3  # recent trend for months beyond the forecast: fixed, so the year projection never depends on the question
SLIDER_LIMITS = {"solar_pct": 40.0, "efficiency_pct": 15.0}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _window(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    return df[(df["date"] >= start) & (df["date"] <= end)]


def _coverage(part: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> float:
    days = (end - start).days + 1
    return len(part) / days if days > 0 else 0.0


def _pct(new: float, old: float) -> float:
    return (new / old - 1) * 100 if old and not math.isnan(old) and not math.isnan(new) else float("nan")


def _nan(v: float) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def feasibility(pct_cut: float) -> str:
    """Label for a reduction effort expressed as % of the current level."""
    if _nan(pct_cut):
        return "unknown"
    return "realistic" if pct_cut <= REALISTIC_PCT else "ambitious" if pct_cut <= AMBITIOUS_PCT else "unrealistic"


def _logmean(a: float, b: float) -> float:
    if a <= 0 or b <= 0:
        return 0.0
    return a if abs(a - b) < 1e-12 else (a - b) / (math.log(a) - math.log(b))


def period_label(f: Fmt, start: pd.Timestamp, end: pd.Timestamp) -> str:
    """'janv.–mars 2026' / 'Jan–Mar 2026', or with both years when it spans two."""
    if start.year == end.year:
        if start.month == end.month:
            return f.month(start)
        return f"{f.month(start, False)}–{f.month(end)}"
    return f"{f.month(start)} – {f.month(end)}"


# ---------------------------------------------------------------------------
# 1. Hotspots
# ---------------------------------------------------------------------------
def hotspots(ctx: DataContext, months: int = 3) -> dict[str, Any]:
    df, last = ctx.df, ctx.last
    start = (last + pd.Timedelta(days=1)) - pd.DateOffset(months=months)
    end = last
    p_start, p_end = start - pd.DateOffset(years=1), end - pd.DateOffset(years=1)
    recent, prev = _window(df, start, end), _window(df, p_start, p_end)
    last12 = df[df["date"] > last - pd.Timedelta(days=365)]
    prev_ok = _coverage(prev, p_start, p_end) >= 0.9

    rows = []
    for key, col in SCOPE_COLS.items():
        r_sum = float(recent[col].sum())
        p_sum = float(prev[col].sum()) if prev_ok else float("nan")
        r_day, y_day = float(recent[col].mean()), float(last12[col].mean())
        # Seasonal rank: the same calendar window in every earlier year with full data
        past = []
        k = 1
        while True:
            s, e = start - pd.DateOffset(years=k), end - pd.DateOffset(years=k)
            if s < ctx.first:
                break
            part = _window(df, s, e)
            if _coverage(part, s, e) >= 0.9:
                past.append((e.year, float(part[col].sum()) * len(recent) / max(len(part), 1)))
            k += 1
        higher = [y for y, v in past if v > r_sum]
        rank = 1 + len(higher)
        flag = "normal"
        if past and rank == 1:
            flag = "record_high"
        elif past and rank == 2 and len(past) >= 3:
            flag = "high"
        elif past and rank == len(past) + 1 and len(past) >= 3:
            flag = "record_low"
        rows.append({
            "key": key, "column": col, "recent_t": r_sum, "previous_t": p_sum, "change_pct": _pct(r_sum, p_sum),
            "per_day_recent": r_day, "per_day_12m": y_day, "vs_12m_pct": _pct(r_day, y_day),
            "rank": rank, "years_compared": len(past) + 1, "highest_since": max(higher) if higher else None,
            "flag": flag,
        })
    total = next(r for r in rows if r["key"] == "total")
    ratio = total["recent_t"] / total["previous_t"] if not _nan(total["previous_t"]) and total["previous_t"] else 1.0
    return {"start": start, "end": end, "previous_start": p_start, "previous_end": p_end, "months": months,
            "days": len(recent), "previous_available": prev_ok, "rows": rows,
            "trend_ratio": float(np.clip(ratio, *TREND_CLIP))}


# ---------------------------------------------------------------------------
# 2. Drivers
# ---------------------------------------------------------------------------
def drivers(ctx: DataContext, hs: dict[str, Any]) -> list[dict[str, Any]]:
    recent = _window(ctx.df, hs["start"], hs["end"])
    prev = _window(ctx.df, hs["previous_start"], hs["previous_end"])
    out = []

    def add(key: str, label: tuple[str, str], unit: str, r: float, p: float) -> None:
        out.append({"key": key, "label": label, "unit": unit, "recent": r, "previous": p, "change_pct": _pct(r, p)})

    for col, label, unit in (("units_produced", ("Production", "Production"), "unités/j"),
                             ("trips", ("Trajets logistiques", "Logistics trips"), "trajets/j")):
        add(col, label, unit, float(recent[col].mean()), float(prev[col].mean()) if hs["previous_available"] else float("nan"))
    for col, act, _ef, _scope, label, unit in SOURCES.values():
        r, p = float(recent[col].mean()), float(prev[col].mean()) if hs["previous_available"] else float("nan")
        add(col, label, f"{unit}/j", r, p)
        ra, pa = float(recent[act].sum()), float(prev[act].sum())
        r_i = float(recent[col].sum()) / ra if ra else float("nan")
        p_i = float(prev[col].sum()) / pa if pa and hs["previous_available"] else float("nan")
        per = "unité" if act == "units_produced" else "trajet"
        add(f"{col}_per_{act}", (f"{label[0]} par {per}", f"{label[1]} per {'unit' if per == 'unité' else 'trip'}"),
            f"{unit}/{per}", r_i, p_i)
    r_int = float(recent["co2e_total_t"].sum()) * 1000 / float(recent["units_produced"].sum() or np.nan)
    p_int = float(prev["co2e_total_t"].sum()) * 1000 / float(prev["units_produced"].sum() or np.nan)
    add("intensity_kg_unit", ("Intensité carbone", "Carbon intensity"), "kgCO₂e/unité", r_int,
        p_int if hs["previous_available"] else float("nan"))
    return out


# ---------------------------------------------------------------------------
# 3. LMDI decomposition (additive, LMDI-I)
# ---------------------------------------------------------------------------
def decompose(ctx: DataContext, hs: dict[str, Any]) -> dict[str, Any] | None:
    """Per-day change between the two windows = volume effect + intensity effect, exactly.

    For each source s: E_s = A_s × (D_s / A_s) × F_s with A = activity (units or trips),
    D = activity data (kWh, L, kg, m³) and F = emission factor (constant here, so no factor effect).
    """
    if not hs["previous_available"]:
        return None
    recent = _window(ctx.df, hs["start"], hs["end"])
    prev = _window(ctx.df, hs["previous_start"], hs["previous_end"])
    ef = ctx.factors
    by_source, vol_total, int_total = [], 0.0, 0.0
    for key, (col, act, ef_key, scope, label, unit) in SOURCES.items():
        f = ef.get(ef_key, 0.0) / 1000.0
        a1, a0 = float(recent[act].mean()), float(prev[act].mean())
        d1, d0 = float(recent[col].mean()), float(prev[col].mean())
        e1, e0 = d1 * f, d0 * f
        if min(a1, a0, d1, d0) > 0:
            w = _logmean(e1, e0)
            vol = w * math.log(a1 / a0)
            inten = w * math.log((d1 / a1) / (d0 / a0))
        else:  # degenerate (zero activity): attribute everything to intensity
            vol, inten = 0.0, e1 - e0
        vol_total += vol
        int_total += inten
        by_source.append({"key": key, "label": label, "scope": scope, "activity": act,
                          "change_t_per_day": e1 - e0, "volume_t_per_day": vol, "intensity_t_per_day": inten,
                          "activity_change_pct": _pct(a1, a0), "intensity_change_pct": _pct(d1 / a1 if a1 else np.nan,
                                                                                            d0 / a0 if a0 else np.nan)})
    total_change = sum(s["change_t_per_day"] for s in by_source)
    return {"change_t_per_day": total_change, "volume_t_per_day": vol_total, "intensity_t_per_day": int_total,
            "days": hs["days"], "change_t_window": total_change * hs["days"], "by_source": by_source}


# ---------------------------------------------------------------------------
# 4. Year projection against the cap
# ---------------------------------------------------------------------------
def _remaining_baseline(ctx: DataContext, year: int, trend: float) -> pd.DataFrame:
    """Daily baseline for the rest of the year: forecast first, then a seasonal estimate
    (same day last year × recent trend) where the forecast stops. Column ``source`` says which."""
    last = ctx.last
    year_end = pd.Timestamp(year, 12, 31)
    days = pd.date_range(max(last + pd.Timedelta(days=1), pd.Timestamp(year, 1, 1)), year_end, freq="D")
    if len(days) == 0:
        return pd.DataFrame(columns=["date", "yhat", "lo90", "hi90", "source"])
    base = pd.DataFrame({"date": days})
    fc = ctx.fc[["date", "yhat", "lo90", "hi90"]] if not ctx.fc.empty else pd.DataFrame(columns=["date", "yhat", "lo90", "hi90"])
    base = base.merge(fc, on="date", how="left")
    base["source"] = np.where(base["yhat"].notna(), "forecast", "estimate")
    est = base["yhat"].isna()
    if est.any():
        hist = ctx.df.set_index("date")["co2e_total_t"]
        ly = base.loc[est, "date"] - pd.DateOffset(years=1)
        vals = hist.reindex(ly).to_numpy(dtype=float)
        fallback = float(hist.tail(365).mean())
        vals = np.where(np.isnan(vals), fallback, vals) * trend
        if not fc.empty and fc["yhat"].sum() > 0:
            lo_r, hi_r = fc["lo90"].sum() / fc["yhat"].sum(), fc["hi90"].sum() / fc["yhat"].sum()
        else:
            lo_r, hi_r = 0.9, 1.1
        base.loc[est, "yhat"] = vals
        base.loc[est, "lo90"] = vals * lo_r
        base.loc[est, "hi90"] = vals * hi_r
    return base


def project_year(ctx: DataContext, hs: dict[str, Any], cap: float | None = None) -> dict[str, Any]:
    last = ctx.last
    year = (last + pd.Timedelta(days=1)).year
    bp = targets.plan(ctx.cfg, ctx.df)
    cap = float(cap if cap is not None else bp.cap(year))
    actual_rows = ctx.df[ctx.df["date"].dt.year == year]
    actual = float(actual_rows["co2e_total_t"].sum())
    base = _remaining_baseline(ctx, year, hs["trend_ratio"])
    fc_part, est_part = base[base["source"] == "forecast"], base[base["source"] == "estimate"]
    total = actual + float(base["yhat"].sum())
    total_lo = actual + float(base["lo90"].sum())
    total_hi = actual + float(base["hi90"].sum())

    months = []
    for m in range(1, 13):
        ms, me = pd.Timestamp(year, m, 1), pd.Timestamp(year, m, 1) + pd.offsets.MonthEnd(0)
        a = float(_window(actual_rows, ms, me)["co2e_total_t"].sum())
        bm = base[(base["date"] >= ms) & (base["date"] <= me)]
        fcast = float(bm[bm["source"] == "forecast"]["yhat"].sum())
        est = float(bm[bm["source"] == "estimate"]["yhat"].sum())
        ceiling = cap * bp.weight(m)
        proj = a + fcast + est
        months.append({"month": ms, "actual": a, "forecast": fcast, "estimate": est, "projected": proj,
                       "ceiling": ceiling, "over_t": proj - ceiling, "over": proj > ceiling + 1e-9,
                       "basis": "actual" if me <= last else ("estimate" if est > 0 and fcast == 0 else "forecast")})
    model = str(ctx.run["model_name"]) if ctx.run is not None else None
    return {
        "year": year, "cap": cap, "actual_ytd": actual, "actual_until": last if not actual_rows.empty else None,
        "forecast_t": float(fc_part["yhat"].sum()),
        "forecast_start": fc_part["date"].min() if not fc_part.empty else None,
        "forecast_end": fc_part["date"].max() if not fc_part.empty else None,
        "forecast_model": model,
        "estimate_t": float(est_part["yhat"].sum()),
        "estimate_start": est_part["date"].min() if not est_part.empty else None,
        "estimate_end": est_part["date"].max() if not est_part.empty else None,
        "estimate_method": "same_period_last_year_x_recent_trend", "trend_ratio": hs["trend_ratio"],
        "total_t": total, "total_lo_t": total_lo, "total_hi_t": total_hi,
        "gap_t": total - cap, "gap_lo_t": total_lo - cap, "gap_hi_t": total_hi - cap,
        "remaining_days": len(base), "remaining_t": float(base["yhat"].sum()),
        "months": months, "baseline": base,
    }


# ---------------------------------------------------------------------------
# 5. Required reductions
# ---------------------------------------------------------------------------
def reductions(ctx: DataContext, hs: dict[str, Any], proj: dict[str, Any], months: int) -> dict[str, Any] | None:
    gap = proj["gap_t"]
    if gap <= 0 or proj["remaining_days"] == 0:
        return None
    ef = ctx.factors
    recent = _window(ctx.df, hs["start"], hs["end"])
    last = ctx.last
    level = {k: float(recent[v[0]].mean()) for k, v in SOURCES.items()}

    def single(key: str, t_per_day: float) -> dict[str, Any]:
        _col, _a, ef_key, *_ = SOURCES[key]
        f = ef.get(ef_key, 0.0)
        qty = t_per_day * 1000 / f if f else float("nan")
        p = qty / level[key] * 100 if level[key] else float("nan")
        return {"source": key, "per_day": qty, "pct": p, "feasibility": feasibility(p)}

    windows = []
    short_end = min(pd.Timestamp(proj["year"], 12, 31), last + pd.DateOffset(months=months))
    short_days = (short_end - last).days
    for key, days, end in (("short", short_days, short_end), ("rest_of_year", proj["remaining_days"],
                                                               pd.Timestamp(proj["year"], 12, 31))):
        if days <= 0:
            continue
        tpd = gap / days
        windows.append({"key": key, "start": last + pd.Timedelta(days=1), "end": end, "days": days,
                        "t_per_day": tpd, "electricity_only": single("electricity", tpd),
                        "fuel_oil_only": single("fuel_oil", tpd)})
    if len(windows) == 2 and windows[0]["days"] == windows[1]["days"]:
        windows = windows[1:]  # the short window already reaches the end of the year

    # Same % cut on every source, shared out by each source's weight in the last 12 months
    last12 = ctx.df[ctx.df["date"] > last - pd.Timedelta(days=365)]
    src_t = {k: float(last12[v[0]].sum()) * ef.get(v[2], 0.0) / 1000 for k, v in SOURCES.items()}
    src_total = sum(src_t.values()) or 1.0
    uniform_pct = gap / proj["remaining_t"] * 100 if proj["remaining_t"] else float("nan")
    uniform_hi_pct = proj["gap_hi_t"] / proj["remaining_t"] * 100 if proj["remaining_t"] else float("nan")
    mix = []
    for key, v in SOURCES.items():
        share = src_t[key] / src_total
        mix.append({"source": key, "label": v[4], "unit": v[5], "share_pct": share * 100, "t": gap * share,
                    "per_day": level[key] * uniform_pct / 100, "current_per_day": level[key]})
    return {"needed_t": gap, "needed_hi_t": proj["gap_hi_t"], "windows": windows,
            "uniform_pct": uniform_pct, "uniform_hi_pct": max(uniform_hi_pct, 0.0),
            "uniform_feasibility": feasibility(uniform_pct), "mix": mix, "current_level": level}


# ---------------------------------------------------------------------------
# 6. Levers (same simulator as the Scenarios page)
# ---------------------------------------------------------------------------
LEVERS = [
    ("efficiency_5", {"efficiency_pct": 5.0}, ("Efficacité énergétique +5 %", "Energy efficiency +5%")),
    ("efficiency_10", {"efficiency_pct": 10.0}, ("Efficacité énergétique +10 %", "Energy efficiency +10%")),
    ("solar_10", {"solar_pct": 10.0}, ("Autoconsommation solaire +10 %", "Solar self-consumption +10%")),
    ("solar_20", {"solar_pct": 20.0}, ("Autoconsommation solaire +20 %", "Solar self-consumption +20%")),
    ("combo", {"solar_pct": 10.0, "efficiency_pct": 5.0}, ("Solaire +10 % et efficacité +5 %", "Solar +10% and efficiency +5%")),
    ("production_-5", {"production_pct": -5.0}, ("Production −5 % (sensibilité)", "Production −5% (sensitivity)")),
]


def levers(ctx: DataContext, proj: dict[str, Any]) -> dict[str, Any]:
    from carbonsync.scenarios.whatif import (
        get_historical_scope_shares,
        simulate_scenario,
    )

    base = proj["baseline"]
    if base.empty:
        return {"options": [], "minimum": {}, "shares": {}}
    try:
        shares = get_historical_scope_shares(ctx.config_path)
    except Exception:  # noqa: BLE001 - fall back to the simulator's own defaults
        shares = None
    gap = proj["gap_t"]
    options = []
    for key, params, label in LEVERS:
        res = simulate_scenario(base[["date", "yhat", "lo90", "hi90"]], solar_share_pct=params.get("solar_pct", 0.0),
                                efficiency_gain_pct=params.get("efficiency_pct", 0.0),
                                production_load_pct=params.get("production_pct", 0.0), scope_shares=shares)
        saved = -float(res["summary"]["net_delta_tco2e"])
        options.append({"key": key, "label": label, "params": params, "saved_t": saved,
                        "gap_covered_pct": saved / gap * 100 if gap > 0 else float("nan"),
                        "closes_gap": gap <= 0 or saved >= gap, "operational": not key.startswith("production")})
    shares = shares or {"scope1_share": 0.25, "scope2_share": 0.65, "other_share": 0.10}
    s1, s2 = shares.get("scope1_share", 0.25), shares.get("scope2_share", 0.65)
    minimum = {}
    if gap > 0 and proj["remaining_t"] > 0:
        need = gap / proj["remaining_t"]           # fraction of the remaining emissions to remove
        eff = need / (s2 + s1 / 2) * 100           # simulator: efficiency cuts electricity fully, fuel by half
        sol = need / s2 * 100 if s2 else float("inf")
        minimum = {"efficiency_pct": eff, "efficiency_in_range": eff <= SLIDER_LIMITS["efficiency_pct"],
                   "solar_pct": sol, "solar_in_range": sol <= SLIDER_LIMITS["solar_pct"]}
    return {"options": options, "minimum": minimum, "shares": shares}


# ---------------------------------------------------------------------------
# Alerts on data that could bias the analysis
# ---------------------------------------------------------------------------
def alerts(ctx: DataContext, hs: dict[str, Any], lang: str) -> list[dict[str, Any]]:
    out = []
    recent = _window(ctx.df, hs["start"], hs["end"])
    p995 = float(ctx.df["co2e_total_t"].quantile(0.995))
    spikes = recent[recent["co2e_total_t"] > p995]
    if not spikes.empty:
        out.append({"key": "spikes", "dates": list(spikes["date"]), "values": list(spikes["co2e_total_t"])})
    imp = int(recent["is_imputed"].fillna(0).astype(int).sum())
    if imp:
        out.append({"key": "imputed", "days": imp, "pct": imp / max(len(recent), 1) * 100})
    q = ctx.insights(lang)["quality"]
    if q["rejected"]:
        out.append({"key": "quarantine", "files": q["rejected"]})
    if q["behind"]:
        out.append({"key": "behind", "departments": q["behind"]})
    if q["pending"]:
        out.append({"key": "pending", "departments": {k: v for k, v in q["pending"].items()}})
    if ctx.run is not None and pd.Timestamp(ctx.run["end_date"]) != ctx.last:
        out.append({"key": "stale_forecast", "trained_until": pd.Timestamp(ctx.run["end_date"])})
    return out


# ---------------------------------------------------------------------------
# Text: findings and action plan
# ---------------------------------------------------------------------------
def _scope_sentence(f: Fmt, r: dict[str, Any], label: str, period: str, prev_period: str) -> str:
    t, num, pct = f.t, f.num, f.pct
    s = t(f"{label} sur {period} : {num(r['recent_t'])} t", f"{label} over {period}: {num(r['recent_t'])} t")
    if not _nan(r["change_pct"]):
        s += t(f" ({pct(r['change_pct'], 1, True)} vs {prev_period})", f" ({pct(r['change_pct'], 1, True)} vs {prev_period})")
    s += t(f", soit {num(r['per_day_recent'], 2)} t/jour contre {num(r['per_day_12m'], 2)} en moyenne sur 12 mois "
           f"({pct(r['vs_12m_pct'], 1, True)})",
           f", i.e. {num(r['per_day_recent'], 2)} t/day against a 12-month average of {num(r['per_day_12m'], 2)} "
           f"({pct(r['vs_12m_pct'], 1, True)})")
    if r["flag"] == "record_high":
        s += t(f". Plus haut niveau jamais mesuré pour cette période ({r['years_compared']} années comparées).",
               f". Highest level ever recorded for this period ({r['years_compared']} years compared).")
    elif r["highest_since"] and r["rank"] <= 3 and r["years_compared"] >= 4:
        s += t(f". Plus haut niveau pour cette période depuis {r['highest_since']} ({r['rank']}e sur {r['years_compared']} années).",
               f". Highest for this period since {r['highest_since']} (#{r['rank']} of {r['years_compared']} years).")
    elif r["flag"] == "record_low":
        s += t(". Plus bas niveau mesuré pour cette période.", ". Lowest level recorded for this period.")
    else:
        s += "."
    return s


def _minus(text: str) -> str:
    """Typographic minus for negative numbers (not inside dates like 2026-03-31)."""
    return re.sub(r"(?<![\w.,])-(?=\d)", "\u2212", text)


def _write(f: Fmt, res: dict[str, Any]) -> tuple[list[str], list[str]]:
    findings, plan = _write_raw(f, res)
    return [_minus(x) for x in findings], [_minus(x) for x in plan]


def _write_raw(f: Fmt, res: dict[str, Any]) -> tuple[list[str], list[str]]:
    t, num, pct = f.t, f.num, f.pct

    def snum(v: float, d: int = 2) -> str:
        return ("+" if v > 0 else "") + num(v, d)
    hs, dec, proj, red, lev = res["hotspots"], res["decomposition"], res["projection"], res["reductions"], res["levers"]
    period = period_label(f, hs["start"], hs["end"])
    prev_period = period_label(f, hs["previous_start"], hs["previous_end"])
    rows = {r["key"]: r for r in hs["rows"]}
    findings: list[str] = []

    # Hotspots: total, then the scopes that matter (Scope 2 and 1)
    findings.append(_scope_sentence(f, rows["total"], t("Émissions totales", "Total emissions"), period, prev_period))
    for key in ("s2", "s1"):
        findings.append(_scope_sentence(f, rows[key], SCOPE_NAMES[key][0 if f.lang == "fr" else 1], period, prev_period))

    # Why it changed
    if dec:
        units = next(d for d in res["drivers"] if d["key"] == "units_produced")
        el = next(d for d in res["drivers"] if d["key"] == "electricity_kwh_per_units_produced")
        findings.append(t(
            f"Explication (méthode LMDI) : {snum(dec['change_t_per_day'])} t/jour d'écart au total, dont "
            f"{snum(dec['volume_t_per_day'])} t/jour dus au volume d'activité (production {pct(units['change_pct'], 1, True)}) "
            f"et {snum(dec['intensity_t_per_day'])} t/jour dus à l'intensité (électricité par unité "
            f"{pct(el['change_pct'], 1, True)}).",
            f"Why (LMDI method): {snum(dec['change_t_per_day'])} t/day change in total, of which "
            f"{snum(dec['volume_t_per_day'])} t/day comes from activity volume (production {pct(units['change_pct'], 1, True)}) "
            f"and {snum(dec['intensity_t_per_day'])} t/day from intensity (electricity per unit {pct(el['change_pct'], 1, True)})."))
        vol, inten = dec["volume_t_per_day"], dec["intensity_t_per_day"]
        clearly = abs(el["change_pct"]) >= 3 if not _nan(el["change_pct"]) else False
        if dec["change_t_per_day"] > 0:
            if vol > 0 and inten <= 0:
                findings.append(t(
                    "La hausse vient donc de l'activité, pas d'une dérive de l'efficacité : l'usine produit plus, et "
                    + ("consomme nettement moins par unité." if clearly else "consomme même un peu moins par unité."),
                    "So the increase comes from activity, not from an efficiency drift: the plant produces more and "
                    + ("uses clearly less per unit." if clearly else "even uses slightly less per unit.")))
            elif inten > 0 and vol <= 0:
                findings.append(t("La hausse vient d'une consommation plus forte par unité, pas du volume : c'est le premier "
                                  "point à corriger.",
                                  "The increase comes from higher consumption per unit, not from volume: that is the "
                                  "first thing to fix."))
            elif inten > 0:
                findings.append(t(f"Hausse à la fois du volume et de la consommation par unité ; la part « intensité » "
                                  f"({snum(inten)} t/jour) est le premier point à corriger.",
                                  f"Both volume and consumption per unit went up; the intensity part ({snum(inten)} t/day) "
                                  "is the first thing to fix."))
        elif inten < 0:
            findings.append(t("La baisse vient de l'efficacité (moins de consommation par unité)"
                              + (", et en partie d'une activité plus faible." if vol < 0 else ", malgré une activité en hausse."),
                              "The decrease comes from efficiency (less consumption per unit)"
                              + (", and partly from lower activity." if vol < 0 else ", despite higher activity.")))
        elif vol < 0:
            findings.append(t("La baisse vient d'une activité plus faible, pas d'un gain d'efficacité.",
                              "The decrease comes from lower activity, not from an efficiency gain."))

    # Projection
    if proj["remaining_days"]:
        parts = t(f"{num(proj['actual_ytd'])} t réalisées", f"{num(proj['actual_ytd'])} t emitted")
        if proj["forecast_t"]:
            parts += t(f" + {num(proj['forecast_t'])} t prévues ({period_label(f, proj['forecast_start'], proj['forecast_end'])}"
                       f", {res['forecast_name']})",
                       f" + {num(proj['forecast_t'])} t forecast ({period_label(f, proj['forecast_start'], proj['forecast_end'])}"
                       f", {res['forecast_name']})")
        if proj["estimate_t"]:
            parts += t(f" + {num(proj['estimate_t'])} t estimées ({period_label(f, proj['estimate_start'], proj['estimate_end'])}"
                       ", même période l'an dernier × tendance récente)",
                       f" + {num(proj['estimate_t'])} t estimated ({period_label(f, proj['estimate_start'], proj['estimate_end'])}"
                       ", same period last year × recent trend)")
        gap = proj["gap_t"]
        verdict = (t(f"dépassement de {num(gap)} t", f"{num(gap)} t over") if gap > 0
                   else t(f"marge de {num(-gap)} t", f"{num(-gap)} t of headroom"))
        findings.append(t(
            f"Projection {proj['year']} : {parts} = {num(proj['total_t'])} t pour un plafond de {num(proj['cap'])} t → {verdict} "
            f"(fourchette prudente à 90 % : {num(proj['total_lo_t'])} à {num(proj['total_hi_t'])} t).",
            f"{proj['year']} projection: {parts} = {num(proj['total_t'])} t against a {num(proj['cap'])} t cap → {verdict} "
            f"(cautious 90% range: {num(proj['total_lo_t'])} to {num(proj['total_hi_t'])} t)."))
        if gap > 0 and proj["gap_lo_t"] > 0:
            findings.append(t("Même le bas de la fourchette dépasse le plafond : le dépassement est quasi certain sans action.",
                              "Even the bottom of the range is above the cap: without action the overshoot is almost certain."))
        if gap <= 0 and proj["gap_hi_t"] > 0:
            findings.append(t(f"Attention : le haut de la fourchette dépasse le plafond de {num(proj['gap_hi_t'])} t.",
                              f"Careful: the top of the range exceeds the cap by {num(proj['gap_hi_t'])} t."))

    # Reductions
    if red:
        labels = {"realistic": t("réaliste", "realistic"), "ambitious": t("ambitieux", "ambitious"),
                  "unrealistic": t("irréaliste", "unrealistic"), "unknown": "—"}
        for w in red["windows"]:
            el = w["electricity_only"]
            if w["key"] == "short":
                findings.append(t(
                    f"Concentrer l'effort sur les {res['months']} prochains mois ({w['days']} jours) demanderait "
                    f"−{num(w['t_per_day'], 2)} t/jour, soit −{num(el['per_day'])} kWh/jour ({pct(-el['pct'], 0)} de l'électricité) "
                    f"si l'électricité porte seule l'effort : {labels[el['feasibility']]}.",
                    f"Packing the effort into the next {res['months']} months ({w['days']} days) would take "
                    f"−{num(w['t_per_day'], 2)} t/day, i.e. −{num(el['per_day'])} kWh/day ({pct(-el['pct'], 0)} of electricity) "
                    f"if electricity carries it alone: {labels[el['feasibility']]}."))
            else:
                findings.append(t(
                    f"Réparti jusqu'au 31 déc. ({w['days']} jours) : −{num(w['t_per_day'], 2)} t/jour, soit "
                    f"−{num(el['per_day'])} kWh/jour ({pct(-el['pct'], 1)}) si l'électricité porte seule l'effort "
                    f"({labels[el['feasibility']]}), ou {pct(-red['uniform_pct'], 1)} sur chaque source "
                    f"({labels[red['uniform_feasibility']]}).",
                    f"Spread to 31 Dec ({w['days']} days): −{num(w['t_per_day'], 2)} t/day, i.e. "
                    f"−{num(el['per_day'])} kWh/day ({pct(-el['pct'], 1)}) if electricity carries it alone "
                    f"({labels[el['feasibility']]}), or {pct(-red['uniform_pct'], 1)} on every source "
                    f"({labels[red['uniform_feasibility']]})."))

    # Levers
    if red and lev["options"]:
        ops = [o for o in lev["options"] if o["operational"]]
        closing = [o for o in ops if o["closes_gap"]]
        best = min(closing, key=lambda o: sum(o["params"].values())) if closing else max(ops, key=lambda o: o["saved_t"])
        mn = lev["minimum"]
        txt = t(f"Leviers (simulateur de la page Scénarios) : « {best['label'][0]} » évite {num(best['saved_t'])} t "
                f"({pct(best['gap_covered_pct'], 0)} de l'écart).",
                f"Levers (Scenarios page simulator): \"{best['label'][1]}\" avoids {num(best['saved_t'])} t "
                f"({pct(best['gap_covered_pct'], 0)} of the gap).")
        if mn:
            txt += t(f" Seul, il faudrait {pct(mn['efficiency_pct'], 1)} d'efficacité énergétique ou "
                     f"{pct(mn['solar_pct'], 1)} d'autoconsommation solaire supplémentaire.",
                     f" On its own, it would take {pct(mn['efficiency_pct'], 1)} energy efficiency or "
                     f"{pct(mn['solar_pct'], 1)} more solar self-consumption.")
        findings.append(txt)

    # Alerts
    for a in res["alerts"]:
        if a["key"] == "spikes":
            findings.append(t(f"{len(a['dates'])} pic(s) anormal(aux) dans la période ({', '.join(f.date(d) for d in a['dates'])}) : "
                              "à vérifier (compteur, panne).",
                              f"{len(a['dates'])} abnormal spike(s) in the period ({', '.join(f.date(d) for d in a['dates'])}): "
                              "check them (meter, breakdown)."))
        elif a["key"] == "imputed" and a["pct"] >= 5:
            findings.append(t(f"{a['days']} jours imputés dans la période ({pct(a['pct'], 0)}) : les chiffres sont en partie estimés.",
                              f"{a['days']} imputed days in the period ({pct(a['pct'], 0)}): figures are partly estimated."))
        elif a["key"] == "stale_forecast":
            findings.append(t(f"La prévision s'arrête aux données du {f.date(a['trained_until'])} : relancez-la pour intégrer "
                              "les derniers dépôts.",
                              f"The forecast was trained on data up to {f.date(a['trained_until'])}: rerun it to include "
                              "the latest uploads."))

    # ---- Action plan ----------------------------------------------------------------
    plan: list[str] = []
    over_months = [m for m in proj["months"] if m["over"] and m["basis"] != "actual"]
    if red:
        w_rest = next((w for w in red["windows"] if w["key"] == "rest_of_year"), red["windows"][-1])
        mix = {m["source"]: m for m in red["mix"]}
        plan.append(t(f"Objectif : {pct(-red['uniform_pct'], 1)} sur chaque source d'ici le 31 déc. "
                      f"(≈ −{num(w_rest['t_per_day'], 2)} t/jour, {num(red['needed_t'])} t au total).",
                      f"Target: {pct(-red['uniform_pct'], 1)} on every source by 31 Dec "
                      f"(≈ −{num(w_rest['t_per_day'], 2)} t/day, {num(red['needed_t'])} t in total)."))
        e = mix["electricity"]
        plan.append(t(f"Électricité ({pct(e['share_pct'], 0)} des émissions) : −{num(e['per_day'])} kWh/jour "
                      f"({pct(-red['uniform_pct'], 1)}) sur {num(e['current_per_day'])} aujourd'hui. Cibler les gros consommateurs, couper les charges à vide, "
                      "suivre les kWh par unité chaque semaine.",
                      f"Electricity ({pct(e['share_pct'], 0)} of emissions): −{num(e['per_day'])} kWh/day "
                      f"({pct(-red['uniform_pct'], 1)}) out of {num(e['current_per_day'])} today. Target the largest consumers, cut idle loads, "
                      "track kWh per unit every week."))
        fo, di = mix["fuel_oil"], mix["diesel"]
        plan.append(t(f"Combustibles (Scope 1) : −{num(fo['per_day'])} L de fioul/jour et −{num(di['per_day'])} L de "
                      "gazole/jour (réglage des brûleurs, tournées logistiques optimisées).",
                      f"Fuels (Scope 1): −{num(fo['per_day'])} L fuel oil/day and −{num(di['per_day'])} L diesel/day "
                      "(burner tuning, optimised logistics rounds)."))
        if lev["options"]:
            ops = [o for o in lev["options"] if o["operational"]]
            closing = [o for o in ops if o["closes_gap"]]
            if closing:
                b = min(closing, key=lambda o: sum(o["params"].values()))
                plan.append(t(f"Levier structurel : « {b['label'][0]} » suffit à couvrir l'écart ({pct(b['gap_covered_pct'], 0)}).",
                              f"Structural lever: \"{b['label'][1]}\" is enough to close the gap ({pct(b['gap_covered_pct'], 0)})."))
            else:
                b = max(ops, key=lambda o: o["saved_t"])
                plan.append(t(f"Levier structurel : « {b['label'][0]} » couvre {pct(b['gap_covered_pct'], 0)} de l'écart ; "
                              "le reste doit venir de la sobriété au quotidien.",
                              f"Structural lever: \"{b['label'][1]}\" covers {pct(b['gap_covered_pct'], 0)} of the gap; "
                              "the rest must come from day-to-day savings."))
    elif proj["remaining_days"]:
        plan.append(t(f"La trajectoire tient le plafond {proj['year']} avec {num(-proj['gap_t'])} t de marge : "
                      "garder le rythme et surveiller chaque mois.",
                      f"The trajectory holds the {proj['year']} cap with {num(-proj['gap_t'])} t of headroom: "
                      "keep the pace and check every month."))
    future = [m for m in proj["months"] if m["basis"] != "actual"]
    if over_months and len(over_months) == len(future) and len(future) >= 3:
        plan.append(t(f"Les {len(future)} mois restants dépassent tous leur plafond : l'écart est structurel, pas saisonnier. "
                      "L'effort doit être continu plutôt que concentré sur quelques mois.",
                      f"All {len(future)} remaining months are above their ceiling: the gap is structural, not seasonal. "
                      "The effort has to be continuous rather than packed into a few months."))
    elif over_months:
        names = ", ".join(f.month(m["month"], False).rstrip(".") for m in over_months)
        plan.append(t(f"Mois prévus au-dessus de leur plafond : {names}. Y lisser la production ou y planifier les arrêts techniques.",
                      f"Months forecast above their ceiling: {names}. Smooth production or schedule maintenance stops there."))
    data_steps = []
    for a in res["alerts"]:
        if a["key"] == "quarantine":
            data_steps.append(t(f"corriger et redéposer les {a['files']} fichiers en quarantaine",
                                f"fix and re-upload the {a['files']} quarantined files"))
        elif a["key"] == "behind":
            data_steps.append(t("relancer les services en retard (" + ", ".join(a["departments"]) + ")",
                                "chase the departments that are behind (" + ", ".join(a["departments"]) + ")"))
        elif a["key"] == "stale_forecast":
            data_steps.append(t("relancer la prévision avec les dernières données", "rerun the forecast on the latest data"))
    if data_steps:
        plan.append(t("Données : ", "Data: ") + " ; ".join(data_steps) + ".")
    plan.append(t("Suivi : comparer chaque mois au plafond (page Budget carbone) et relancer cette analyse après chaque dépôt.",
                  "Follow-up: compare each month with its ceiling (Carbon budget page) and rerun this analysis after each upload."))
    return findings, plan


# ---------------------------------------------------------------------------
def analyse(lang: str = "fr", months: int = 3, cap: float | None = None, run_id: str | None = None,
            ctx: DataContext | None = None) -> dict[str, Any]:
    """Full analysis. ``months`` = window studied and short-term action window; ``cap`` overrides the annual cap."""
    months = int(min(max(months, 1), 12))
    ctx = ctx or load_context(run_id)
    f = Fmt(lang)
    with step(log, f"Analyse ({months} months, {f.lang})"):
        hs = hotspots(ctx, months)
        res: dict[str, Any] = {
            "lang": f.lang, "as_of": ctx.last, "months": months, "hotspots": hs,
            "drivers": drivers(ctx, hs), "decomposition": decompose(ctx, hs),
        }
        from carbonsync.export.insights import MODEL_NAMES

        res["forecast_name"] = MODEL_NAMES.get(str(ctx.run["model_name"]), str(ctx.run["model_name"])) if ctx.run is not None else "—"
        trend = hs if months == TREND_MONTHS else hotspots(ctx, TREND_MONTHS)
        res["projection"] = project_year(ctx, trend, cap)
        res["reductions"] = reductions(ctx, hs, res["projection"], months)
        res["levers"] = levers(ctx, res["projection"])
        res["alerts"] = alerts(ctx, hs, f.lang)
        res["findings"], res["plan"] = _write(f, res)
    return res


def render_text(res: dict[str, Any]) -> str:
    """Plain-text version (terminal, built-in assistant, logs)."""
    f = Fmt(res["lang"])
    t = f.t
    hs = res["hotspots"]
    lines = [t(f"ANALYSE CARBONE — données au {f.date(res['as_of'])} · période étudiée : {period_label(f, hs['start'], hs['end'])}",
               f"CARBON ANALYSIS — data to {f.date(res['as_of'])} · period studied: {period_label(f, hs['start'], hs['end'])}"),
             "", t("Constats", "Findings")]
    lines += [f"  {k}. {s}" for k, s in enumerate(res["findings"], 1)]
    lines += ["", t("Plan d'action", "Action plan")]
    lines += [f"  {k}. {s}" for k, s in enumerate(res["plan"], 1)]
    return "\n".join(lines)
