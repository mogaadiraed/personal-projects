"""Everything the reports say, computed once: figures, tables, conclusions and recommendations.

``build_insights(lang)`` reads DuckDB + the JSON side files (read-only) and returns a plain dict used
by the Excel workbook, the PDF brief and the Reports page, so all three always agree.
"""
from __future__ import annotations

import calendar
import json
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd
import yaml

from carbonsync import targets
from carbonsync.db import connect as db_connect
from carbonsync.inventory import breakdown, scope2, scope3
from carbonsync.models.router import router_rule
from carbonsync.obs import get_logger

log = get_logger(__name__)

SCOPES = ["co2e_scope1_t", "co2e_scope2_t", "co2e_other_t"]
DRIVERS = ["electricity_kwh", "fuel_oil_l", "diesel_l", "waste_kg", "water_m3", "units_produced"]
DEPT_TABLES = {"energie": "raw_energy", "production": "raw_production",
               "logistique": "raw_logistics", "dechets_eau": "raw_waste_water"}
DEPT_NAMES = {"energie": ("Énergie", "Energy"), "production": ("Production", "Production"),
              "logistique": ("Logistique", "Logistics"), "dechets_eau": ("Déchets & eau", "Waste & water")}
MODEL_NAMES = {"ens_pc": "Ens-PC", "chronos": "Chronos-2", "prophet": "Prophet",
               "seasonal_naive": "Seasonal naive", "oracle": "Oracle"}
MONTHS = {"fr": ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."],
          "en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]}


def get_project_root() -> Path:
    return Path(__file__).resolve().parents[3]


class Fmt:
    """Locale-aware formatting for generated text."""

    def __init__(self, lang: str):
        self.lang = lang if lang in ("fr", "en") else "en"

    def t(self, fr: str, en: str) -> str:
        return fr if self.lang == "fr" else en

    def num(self, v: float | None, d: int = 0) -> str:
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return "—"
        s = f"{v:,.{d}f}"
        return s.replace(",", " ").replace(".", ",") if self.lang == "fr" else s

    def pct(self, v: float | None, d: int = 1, signed: bool = False) -> str:
        if v is None or (isinstance(v, float) and np.isnan(v)):
            return "—"
        sign = "+" if signed and v > 0 else ""
        return f"{sign}{self.num(v, d)}{' %' if self.lang == 'fr' else '%'}"

    def month(self, ts: pd.Timestamp, year: bool = True) -> str:
        m = MONTHS[self.lang][ts.month - 1]
        return f"{m} {ts.year}" if year else m

    def date(self, ts: pd.Timestamp) -> str:
        ts = pd.Timestamp(ts)
        return f"{ts.day} {MONTHS['fr'][ts.month - 1]} {ts.year}" if self.lang == "fr" else ts.strftime("%d %b %Y")


def _load_config(config_path: Path | None = None) -> dict[str, Any]:
    path = config_path or get_project_root() / "config.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _pick_run(runs: pd.DataFrame, last: pd.Timestamp, history_days: int, cfg: dict[str, Any] | None,
              run_id: str | None) -> pd.Series | None:
    """Same rule as the dashboard: requested run, else a run trained up to the last day, router model first."""
    from carbonsync.models.router import choose_model

    if runs.empty:
        return None
    if run_id and (runs["run_id"] == run_id).any():
        return runs[runs["run_id"] == run_id].iloc[0]
    aligned = runs[pd.to_datetime(runs["end_date"]) == last]
    pool = aligned if not aligned.empty else runs
    preferred = choose_model(history_days, cfg)
    pref = pool[pool["model_name"] == preferred]
    return (pref if not pref.empty else pool).iloc[0]


# ---------------------------------------------------------------------------
def build_insights(lang: str = "fr", run_id: str | None = None, config_path: Path | None = None) -> dict[str, Any]:
    f = Fmt(lang)
    t = f.t
    root = get_project_root()
    cfg = _load_config(config_path)
    db_path = root / cfg.get("paths", {}).get("duckdb", "data/carbonsync.duckdb")

    conn = db_connect(str(db_path), read_only=True)
    try:
        df = conn.execute("SELECT * FROM emissions_daily ORDER BY date").fetchdf()
        runs = conn.execute("SELECT * FROM forecast_runs ORDER BY created_at DESC").fetchdf()
        dept_last = {}
        for d, tbl in DEPT_TABLES.items():
            try:
                v = conn.execute(f"SELECT MAX(date) FROM {tbl}").fetchone()[0]
            except duckdb.Error:
                v = None
            dept_last[d] = pd.Timestamp(v) if v is not None else None
        df["date"] = pd.to_datetime(df["date"])
        last = df["date"].max()
        run = _pick_run(runs, last, len(df), cfg, run_id)
        fc = pd.DataFrame(columns=["date", "horizon", "yhat", "lo90", "hi90"])
        if run is not None:
            fc = conn.execute("SELECT date, horizon, yhat, lo90, hi90 FROM forecasts WHERE run_id = ? ORDER BY horizon",
                              [str(run["run_id"])]).fetchdf()
            fc["date"] = pd.to_datetime(fc["date"])
    finally:
        conn.close()

    first = df["date"].min()
    days = len(df)
    imputed = int(df["is_imputed"].fillna(0).astype(int).sum())

    # ---- Totals & YoY -----------------------------------------------------------
    last12 = df[df["date"] > last - pd.Timedelta(days=365)]
    prev12 = df[(df["date"] <= last - pd.Timedelta(days=365)) & (df["date"] > last - pd.Timedelta(days=730))]
    tot12, totp = float(last12["co2e_total_t"].sum()), float(prev12["co2e_total_t"].sum())
    yoy = (tot12 / totp - 1) * 100 if totp and len(prev12) >= 360 else float("nan")
    shares = {s: float(last12[s].sum()) / tot12 * 100 if tot12 else 0.0 for s in SCOPES}

    # ---- Annual ------------------------------------------------------------------
    annual = df.groupby(df["date"].dt.year).agg(
        s1=("co2e_scope1_t", "sum"), s2=("co2e_scope2_t", "sum"), other=("co2e_other_t", "sum"),
        total=("co2e_total_t", "sum"), days=("date", "count"), kwh=("electricity_kwh", "sum"),
        units=("units_produced", "sum"), imputed=("is_imputed", "sum"),
    )
    annual.index.name = "year"
    annual["complete"] = annual["days"] >= 365
    annual["yoy_pct"] = annual["total"].pct_change() * 100
    annual.loc[~annual["complete"] | ~annual["complete"].shift(1, fill_value=False), "yoy_pct"] = np.nan
    annual["intensity_kg_unit"] = annual["total"] * 1000 / annual["units"].replace(0, np.nan)
    annual = annual.reset_index()

    # ---- Monthly -----------------------------------------------------------------
    month_key = df["date"].dt.to_period("M")
    monthly = df.groupby(month_key).agg(
        s1=("co2e_scope1_t", "sum"), s2=("co2e_scope2_t", "sum"), other=("co2e_other_t", "sum"),
        total=("co2e_total_t", "sum"), days=("date", "count"), imputed=("is_imputed", "sum"),
        **{d: (d, "sum") for d in DRIVERS},
    )
    monthly["complete"] = [n >= p.days_in_month for p, n in zip(monthly.index, monthly["days"])]
    prev_year = monthly["total"].shift(12)
    monthly["yoy_pct"] = (monthly["total"] / prev_year - 1) * 100
    monthly["intensity_kg_unit"] = monthly["total"] * 1000 / monthly["units_produced"].replace(0, np.nan)
    monthly.index = monthly.index.to_timestamp()
    monthly.index.name = "month"
    monthly = monthly.reset_index()
    full = monthly[monthly["complete"]]
    peak_m = full.loc[full["total"].idxmax()] if not full.empty else None
    low_m = full.loc[full["total"].idxmin()] if not full.empty else None
    peak_day = df.loc[df["co2e_total_t"].idxmax()]

    # ---- Intensity & levers ------------------------------------------------------------
    def intensity(part: pd.DataFrame) -> float:
        u = part["units_produced"].sum()
        return float(part["co2e_total_t"].sum() * 1000 / u) if u else float("nan")

    int12, intp = intensity(last12), intensity(prev12) if len(prev12) >= 360 else float("nan")
    gen = cfg.get("generator", {})
    levers = []
    for key, start_s, label, col, per_unit in (
        ("solar", gen.get("solar_start"), t("Solaire sur site", "On-site solar"), "co2e_scope2_t", False),
        ("efficiency", gen.get("efficiency_start"), t("Programme d'efficacité", "Efficiency programme"),
         "electricity_kwh", True),
    ):
        if not start_s:
            continue
        s = pd.Timestamp(start_s)
        before = df[(df["date"] < s) & (df["date"] >= s - pd.Timedelta(days=365))]
        after = df[(df["date"] >= s) & (df["date"] < s + pd.Timedelta(days=365))]
        if len(before) < 300 or len(after) < 300:
            continue
        if per_unit:
            b = before[col].sum() / before["units_produced"].sum()
            a = after[col].sum() / after["units_produced"].sum()
        else:
            b, a = before[col].mean(), after[col].mean()
        levers.append({"key": key, "label": label, "start": s, "before": float(b), "after": float(a),
                       "change_pct": float((a / b - 1) * 100) if b else float("nan")})

    # ---- Forecast ----------------------------------------------------------------------
    forecast: dict[str, Any] = {"available": not fc.empty}
    if not fc.empty:
        recent = df[df["date"] > last - pd.Timedelta(days=len(fc))]
        recent_mean = float(recent["co2e_total_t"].mean())
        fm = fc.groupby(fc["date"].dt.to_period("M")).agg(yhat=("yhat", "sum"), lo90=("lo90", "sum"),
                                                          hi90=("hi90", "sum"), days=("date", "count"))
        fm.index = fm.index.to_timestamp()
        fm.index.name = "month"
        pk = fc.loc[fc["yhat"].idxmax()]
        forecast.update({
            "run_id": str(run["run_id"]), "model": str(run["model_name"]),
            "model_name": MODEL_NAMES.get(str(run["model_name"]), str(run["model_name"])),
            "history_days": int(run["history_days"]), "created_at": pd.Timestamp(run["created_at"]),
            "trained_until": pd.Timestamp(run["end_date"]), "stale": pd.Timestamp(run["end_date"]) != last,
            "start": fc["date"].min(), "end": fc["date"].max(), "days": len(fc),
            "total": float(fc["yhat"].sum()), "lo_total": float(fc["lo90"].sum()), "hi_total": float(fc["hi90"].sum()),
            "mean": float(fc["yhat"].mean()), "recent_mean": recent_mean,
            "change_pct": float((fc["yhat"].mean() / recent_mean - 1) * 100) if recent_mean else float("nan"),
            "width": float((fc["hi90"] - fc["lo90"]).mean()), "peak_date": pk["date"], "peak": float(pk["yhat"]),
            "daily": fc, "monthly": fm.reset_index(),
        })

    # ---- Budget (year being managed): cap from the path to the target, seasonal monthly weights ----------
    bp = targets.plan(cfg, df)
    year = (last + pd.Timedelta(days=1)).year
    cap = bp.cap(year)
    fc_by_day = fc.set_index("date") if not fc.empty else pd.DataFrame(columns=["yhat", "hi90"])
    obs_by_day = df.set_index("date")["co2e_total_t"]
    b_rows = []
    for m in range(1, 13):
        n = calendar.monthrange(year, m)[1]
        rng = pd.date_range(f"{year}-{m:02d}-01", periods=n, freq="D")
        actual = float(obs_by_day.reindex(rng).dropna().sum())
        observed = int(obs_by_day.reindex(rng).notna().sum())
        fdays = fc_by_day.reindex(rng[rng > last]) if not fc_by_day.empty else pd.DataFrame()
        fcast = float(fdays["yhat"].dropna().sum()) if not fdays.empty else 0.0
        upper = float(fdays["hi90"].dropna().sum()) if not fdays.empty else 0.0
        covered = observed + (int(fdays["yhat"].notna().sum()) if not fdays.empty else 0) >= n
        ceiling = cap * bp.weight(m)
        proj = actual + fcast
        status = "nodata" if not covered else "green" if actual + upper <= ceiling else "amber" if proj <= ceiling else "red"
        b_rows.append({"month": pd.Timestamp(year, m, 1), "actual": actual, "forecast": fcast, "projected": proj,
                       "upper": actual + upper, "ceiling": ceiling, "status": status, "observed_days": observed,
                       "covered": covered})
    budget = pd.DataFrame(b_rows)
    cov = budget[budget["covered"]]
    budget_info = {
        "year": year, "cap": cap, "actual_ytd": float(budget["actual"].sum()),
        "projected_covered": float(cov["projected"].sum()), "ceiling_covered": float(cov["ceiling"].sum()),
        "covered_until": cov["month"].max() if not cov.empty else None,
        "red": budget[budget["status"] == "red"]["month"].tolist(),
        "amber": budget[budget["status"] == "amber"]["month"].tolist(),
        "table": budget, "cap_source": bp.cap_source, "weights_source": bp.weights_source,
    }

    # ---- Target and path, Scope 2 both methods, Scope 3 screening, breakdown (last 12 months) ----------
    path = bp.path_table(until=year)
    targets_info = {**bp.as_dict(year), "describe": bp.describe(f.lang), "path": path,
                    "on_track_last": None}
    done = path.dropna(subset=["actual"])
    if not done.empty:
        lastrow = done.iloc[-1]
        targets_info["on_track_last"] = {"year": int(lastrow["year"]), "actual": float(lastrow["actual"]),
                                         "cap": float(lastrow["cap"]), "gap": float(lastrow["gap"]),
                                         "on_track": bool(lastrow["on_track"])}
    w_start = last - pd.Timedelta(days=364)
    s2 = scope2.totals(df, cfg, w_start, last)
    s2["note"] = scope2.note(s2, f.lang)
    s2_years = []
    for y in annual["year"]:
        ty = scope2.totals(df, cfg, f"{int(y)}-01-01", f"{int(y)}-12-31")
        s2_years.append({"year": int(y), "location_t": ty["location_t"], "market_t": ty["market_t"]})
    s2["annual"] = pd.DataFrame(s2_years)
    s3 = scope3.summary(df, cfg, w_start, last)
    s3["table"] = scope3.screening(df, cfg, w_start, last)
    bd_zone = breakdown.by_zone(df, cfg, w_start, last)
    bd_item = breakdown.by_item(df, cfg, w_start, last)

    # ---- Models (backtest) ---------------------------------------------------------------
    bt = _read_json(root / "data" / "backtest_summary.json", {})
    res = pd.DataFrame(bt.get("results", []) if isinstance(bt, dict) else bt)
    models = pd.DataFrame()
    if not res.empty:
        models = res.groupby("model_name")[["mae", "mase", "coverage", "mean_width"]].mean().sort_values("mase")
        models["windows"] = res.groupby("model_name").size()
        models = models.reset_index()
        models["name"] = models["model_name"].map(lambda m: MODEL_NAMES.get(m, m))
    real = models[~models["model_name"].isin(["oracle"])] if not models.empty else models
    best = real.iloc[0] if not real.empty else None

    # ---- Data quality ---------------------------------------------------------------------
    dq = _read_json(root / "data" / "data_quality_report.json", {})
    quarantine = []
    for p in sorted((root / cfg.get("paths", {}).get("quarantine", "data/quarantine")).glob("*.errors.json")):
        q = _read_json(p, {})
        if q:
            quarantine.append({"file": q.get("file_name", p.stem), "department": q.get("department", ""),
                               "reason": (q.get("errors") or ["—"])[0], "fix": q.get("suggested_fix", "")})
    scanned = int(dq.get("total_files_scanned", 0) or 0)
    accepted = int(dq.get("accepted_count", 0) or 0)
    pending = {d: v for d, v in dept_last.items() if v is not None and v > last}
    behind = {d: (last - v).days for d, v in dept_last.items() if v is not None and v < last}

    uploads = _read_json(root / "data" / "uploads_manifest.json", [])
    history = []
    hist_path = root / "data" / "pipeline_history.jsonl"
    if hist_path.exists():
        for line in hist_path.read_text(encoding="utf-8").splitlines()[-15:]:
            try:
                history.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    out: dict[str, Any] = {
        "lang": f.lang, "generated_at": datetime.now(), "site": cfg.get("app", {}).get("title", "CarbonSync AI"),
        "synthetic": cfg.get("app", {}).get("data_mode", "synthetic") == "synthetic",
        "period": {"first": first, "last": last, "days": days, "imputed": imputed,
                   "imputed_pct": imputed / days * 100 if days else 0.0},
        "totals": {"all": float(df["co2e_total_t"].sum()), "last12": tot12, "prev12": totp, "yoy_pct": yoy,
                   "daily_mean_12": float(last12["co2e_total_t"].mean()), "shares": shares,
                   "s1_12": float(last12["co2e_scope1_t"].sum()), "s2_12": float(last12["co2e_scope2_t"].sum()),
                   "other_12": float(last12["co2e_other_t"].sum()),
                   "window": (last12["date"].min(), last), "intensity_12": int12, "intensity_prev": intp,
                   "kwh_12": float(last12["electricity_kwh"].sum()), "units_12": float(last12["units_produced"].sum())},
        "annual": annual, "monthly": monthly,
        "peaks": {"month": peak_m, "low_month": low_m, "day": peak_day},
        "levers": levers, "forecast": forecast, "budget": budget_info, "targets": targets_info,
        "scope2": s2, "scope3": s3,
        "breakdown": {"zones": bd_zone, "items": bd_item, "window": (w_start, last),
                      "errors": breakdown.validate(breakdown.settings(cfg))},
        "alerts": _read_json(root / "data" / "alerts.json", {}),
        "models": models, "best_model": best, "backtest_meta": bt.get("meta", {}) if isinstance(bt, dict) else {},
        "router": router_rule(cfg, days),
        "router_margin_mase": float((cfg.get("alerts") or {}).get("router_margin_mase", 0.05)),
        "quality": {"scanned": scanned, "accepted": accepted, "rejected": int(dq.get("rejected_count", 0) or 0),
                    "warnings": int(dq.get("total_warnings", 0) or 0),
                    "acceptance_pct": accepted / scanned * 100 if scanned else float("nan"),
                    "quarantine": quarantine, "dept_last": dept_last, "pending": pending, "behind": behind},
        "uploads": uploads, "history": history, "factors": cfg.get("emission_factors", {}),
        "factors_review": str(cfg.get("governance", {}).get("factors_review_date", "—")),
    }
    out["conclusions"], out["recommendations"] = _conclusions(out, f)
    log.info("Report insights built: %d days, %d conclusions, forecast %s", days, len(out["conclusions"]),
             forecast.get("run_id", "none"))
    return out


# ---------------------------------------------------------------------------
def _conclusions(i: dict[str, Any], f: Fmt) -> tuple[list[str], list[str]]:
    t, num, pct = f.t, f.num, f.pct
    tot, fc, bud, q = i["totals"], i["forecast"], i["budget"], i["quality"]
    w0, w1 = tot["window"]
    c: list[str] = []

    trend = ""
    if not np.isnan(tot["yoy_pct"]):
        trend = t(f", soit {pct(tot['yoy_pct'], 1, True)} par rapport aux 12 mois précédents",
                  f", {pct(tot['yoy_pct'], 1, True)} versus the previous 12 months")
    c.append(t(f"Sur les 12 derniers mois ({f.month(w0)} – {f.month(w1)}), le site a émis {num(tot['last12'])} tCO₂e"
               f"{trend} (moyenne {num(tot['daily_mean_12'], 2)} t/jour).",
               f"Over the last 12 months ({f.month(w0)} – {f.month(w1)}) the site emitted {num(tot['last12'])} tCO₂e"
               f"{trend} (average {num(tot['daily_mean_12'], 2)} t/day)."))

    sh = tot["shares"]
    c.append(t(f"Le Scope 2 (électricité du réseau) pèse {pct(sh['co2e_scope2_t'], 0)} des émissions, le Scope 1 "
               f"(fioul, diesel) {pct(sh['co2e_scope1_t'], 0)} et les autres postes (déchets, eau) "
               f"{pct(sh['co2e_other_t'], 0)} : l'électricité reste le premier levier.",
               f"Scope 2 (grid electricity) accounts for {pct(sh['co2e_scope2_t'], 0)} of emissions, Scope 1 "
               f"(fuel oil, diesel) {pct(sh['co2e_scope1_t'], 0)} and other sources (waste, water) "
               f"{pct(sh['co2e_other_t'], 0)}: electricity remains the main lever."))

    if not np.isnan(tot["intensity_12"]):
        delta = ""
        if not np.isnan(tot["intensity_prev"]):
            delta = t(f" ({pct((tot['intensity_12'] / tot['intensity_prev'] - 1) * 100, 1, True)} sur un an)",
                      f" ({pct((tot['intensity_12'] / tot['intensity_prev'] - 1) * 100, 1, True)} year on year)")
        c.append(t(f"Intensité carbone : {num(tot['intensity_12'], 1)} kgCO₂e par unité produite{delta}.",
                   f"Carbon intensity: {num(tot['intensity_12'], 1)} kgCO₂e per unit produced{delta}."))

    for lv in i["levers"]:
        what = t("le Scope 2 journalier moyen", "average daily Scope 2") if lv["key"] == "solar" \
            else t("l'électricité consommée par unité produite", "electricity used per unit produced")
        c.append(t(f"{lv['label']} ({f.month(lv['start'])}) : {what} a évolué de {pct(lv['change_pct'], 1, True)} "
                   f"entre l'année qui précède et l'année qui suit.",
                   f"{lv['label']} ({f.month(lv['start'])}): {what} changed by {pct(lv['change_pct'], 1, True)} "
                   f"between the year before and the year after."))

    if fc.get("available"):
        c.append(t(f"Prévision {fc['model_name']} : {num(fc['total'])} tCO₂e sur les {fc['days']} prochains jours "
                   f"(intervalle 90 % : {num(fc['lo_total'])}–{num(fc['hi_total'])} t), soit "
                   f"{num(fc['mean'], 2)} t/jour ({pct(fc['change_pct'], 1, True)} vs la même durée passée) ; "
                   f"pic attendu le {f.date(fc['peak_date'])}.",
                   f"{fc['model_name']} forecast: {num(fc['total'])} tCO₂e over the next {fc['days']} days "
                   f"(90% interval {num(fc['lo_total'])}–{num(fc['hi_total'])} t), i.e. {num(fc['mean'], 2)} t/day "
                   f"({pct(fc['change_pct'], 1, True)} vs the same span just before); peak expected on "
                   f"{f.date(fc['peak_date'])}."))

    if bud["covered_until"] is not None:
        last = i["period"]["last"]
        done = [m for m in bud["red"] if m + pd.offsets.MonthEnd(0) <= last]
        ahead = [m for m in bud["red"] if m not in done]

        def names(ms: list) -> str:
            return ", ".join(f.month(m, False) for m in ms)

        parts = []
        if done:
            parts.append(t(f"{len(done)} mois déjà au-dessus de leur plafond ({names(done)})",
                           f"{len(done)} month(s) already above their ceiling ({names(done)})"))
        if ahead:
            parts.append(t(f"{len(ahead)} mois prévu(s) au-dessus ({names(ahead)})",
                           f"{len(ahead)} month(s) forecast above ({names(ahead)})"))
        over_txt = t(" et ", " and ").join(parts) if parts else t("aucun mois au-dessus de son plafond",
                                                                   "no month above its ceiling")
        c.append(t(f"Budget {bud['year']} ({num(bud['cap'])} t/an) : {num(bud['actual_ytd'])} t déjà émises ; jusqu'à fin "
                   f"{f.month(bud['covered_until'])}, projection {num(bud['projected_covered'])} t pour "
                   f"{num(bud['ceiling_covered'])} t de plafonds cumulés — {over_txt}.",
                   f"{bud['year']} budget ({num(bud['cap'])} t/yr): {num(bud['actual_ytd'])} t emitted so far; through "
                   f"{f.month(bud['covered_until'])}, projected {num(bud['projected_covered'])} t against "
                   f"{num(bud['ceiling_covered'])} t of cumulative ceilings — {over_txt}."))

    tg = i.get("targets") or {}
    if tg.get("base_emissions_t") is not None:
        lt = tg.get("on_track_last")
        track = ""
        if lt:
            track = t(f" En {lt['year']}, {num(lt['actual'])} t pour un plafond de {num(lt['cap'])} t sur la trajectoire "
                      f"({'dans' if lt['on_track'] else 'au-dessus de'} la trajectoire, écart {('+' if lt['gap'] > 0 else '') + num(lt['gap'])} t).",
                      f" In {lt['year']}, {num(lt['actual'])} t against a {num(lt['cap'])} t cap on the path "
                      f"({'on' if lt['on_track'] else 'above the'} path, gap {('+' if lt['gap'] > 0 else '') + num(lt['gap'])} t).")
        c.append(t(f"Objectif : −{tg['reduction_pct']:g} % en {tg['target_year']} par rapport à {tg['base_year']} "
                   f"({num(tg['base_emissions_t'])} → {num(tg['target_emissions_t'])} t) ; plafond {tg['year']} sur la "
                   f"trajectoire linéaire : {num(tg['annual_cap_t'])} t.{track}",
                   f"Target: −{tg['reduction_pct']:g}% by {tg['target_year']} vs {tg['base_year']} "
                   f"({num(tg['base_emissions_t'])} → {num(tg['target_emissions_t'])} t); {tg['year']} cap on the "
                   f"straight-line path: {num(tg['annual_cap_t'])} t.{track}"))

    s2, s3 = i.get("scope2") or {}, i.get("scope3") or {}
    if s2:
        c.append(t(f"Scope 2 sur 12 mois : {num(s2['location_t'])} t (méthode localisation) et {num(s2['market_t'])} t "
                   f"(méthode marché). {s2['note']}",
                   f"Scope 2 over 12 months: {num(s2['location_t'])} t location-based and {num(s2['market_t'])} t "
                   f"market-based. {s2['note']}"))
    if s3:
        top = s3["by_category"][0] if s3.get("by_category") else None
        top_txt = ""
        if top:
            top_txt = t(f" ; premier poste : catégorie {top['category']} ({top['category_fr'].lower()}, {num(top['t'])} t)",
                        f"; largest: category {top['category']} ({top['category_en'].lower()}, {num(top['t'])} t)")
        c.append(t(f"Présélection Scope 3 (12 mois) : environ {num(s3['total_t'])} t, dont {num(s3['calculated_t'])} t "
                   f"calculées sur données du site (déchets et eau, déjà comptés dans le budget) et {num(s3['estimated_t'])} t "
                   f"estimées à partir d'hypothèses"
                   f"{top_txt}. Hors périmètre du budget ; ordres de grandeur à confirmer.",
                   f"Scope 3 screening (12 months): about {num(s3['total_t'])} t, of which {num(s3['calculated_t'])} t "
                   f"calculated from site data (waste and water, already in the budget) and {num(s3['estimated_t'])} t "
                   f"estimated from assumptions{top_txt}. "
                   f"Outside the budget perimeter; orders of magnitude to confirm."))

    best = i["best_model"]
    if best is not None:
        n_orig = len(i["backtest_meta"].get("origins") or [])
        scope_fr = (f"{n_orig} origines × {int(best['windows'])} historiques" if n_orig
                    else f"{int(best['windows'])} historiques")
        scope_en = (f"{n_orig} origins × {int(best['windows'])} history windows" if n_orig
                    else f"{int(best['windows'])} history windows")
        models = i["models"]
        chosen = models[models["model_name"] == i["router"]["choice"]]
        r = chosen.iloc[0] if not chosen.empty else None
        if r is None or r["model_name"] == best["model_name"]:
            c.append(t(f"En backtest ({scope_fr}), {best['name']} est le plus précis (MASE {num(best['mase'], 2)}, "
                       f"couverture 90 % réelle {pct(best['coverage'] * 100, 0)}). Règle de production : {i['router']['fr']}.",
                       f"In backtest ({scope_en}), {best['name']} is the most accurate (MASE {num(best['mase'], 2)}, "
                       f"actual 90% coverage {pct(best['coverage'] * 100, 0)}). Production rule: {i['router']['en']}."))
        elif r["mase"] - best["mase"] <= i["router_margin_mase"]:
            c.append(t(f"En backtest ({scope_fr}), {best['name']} (MASE {num(best['mase'], 3)}) et {r['name']} "
                       f"({num(r['mase'], 3)}) sont à égalité de précision. {r['name']} est retenu : son intervalle 90 % "
                       f"contient {pct(r['coverage'] * 100, 0)} des jours réels (cible 90 %, {best['name']} "
                       f"{pct(best['coverage'] * 100, 0)}) et il ne demande qu'un modèle. Règle : {i['router']['fr']}.",
                       f"In backtest ({scope_en}), {best['name']} (MASE {num(best['mase'], 3)}) and {r['name']} "
                       f"({num(r['mase'], 3)}) are level on accuracy. {r['name']} is used: its 90% interval holds "
                       f"{pct(r['coverage'] * 100, 0)} of real days (target 90%, {best['name']} "
                       f"{pct(best['coverage'] * 100, 0)}) and it needs a single model. Rule: {i['router']['en']}."))
        else:
            c.append(t(f"En backtest ({scope_fr}), {best['name']} (MASE {num(best['mase'], 2)}) est plus précis que "
                       f"{r['name']}, le modèle du routeur ({num(r['mase'], 2)}) : revoir le routeur (Configuration → "
                       f"Système).",
                       f"In backtest ({scope_en}), {best['name']} (MASE {num(best['mase'], 2)}) beats {r['name']}, the "
                       f"router's model ({num(r['mase'], 2)}): review the router (Configuration → System)."))

    late = ", ".join(f"{t(*DEPT_NAMES[d])} ({n} {t('j', 'd')})" for d, n in q["behind"].items())
    c.append(t(f"Qualité des données : {q['accepted']}/{q['scanned']} fichiers acceptés "
               f"({pct(q['acceptance_pct'], 1)}), {q['rejected']} en quarantaine, "
               f"{pct(i['period']['imputed_pct'], 1)} de jours imputés"
               + (f" ; en retard : {late}." if late else "."),
               f"Data quality: {q['accepted']}/{q['scanned']} files accepted ({pct(q['acceptance_pct'], 1)}), "
               f"{q['rejected']} quarantined, {pct(i['period']['imputed_pct'], 1)} imputed days"
               + (f"; behind: {late}." if late else ".")))

    r: list[str] = []
    if sh["co2e_scope2_t"] >= 50:
        r.append(t("Électricité : étendre le solaire sur site, envisager un contrat d'électricité verte (PPA, offre verte : "
                   "compté dans le Scope 2 méthode marché) et cibler les postes les plus consommateurs (Scope 2 majoritaire).",
                   "Electricity: extend on-site solar, consider a green-electricity contract (PPA, green tariff: counted "
                   "in market-based Scope 2) and target the largest consumers (Scope 2 dominates)."))
    lt = (i.get("targets") or {}).get("on_track_last")
    if lt and not lt["on_track"]:
        r.append(t(f"Trajectoire 2030 : {lt['year']} a dépassé son plafond de {num(lt['gap'])} t ; prioriser les leviers "
                   "de la page Répartition (zones et postes les plus émetteurs) et les chiffrer dans Scénarios.",
                   f"2030 path: {lt['year']} exceeded its cap by {num(lt['gap'])} t; prioritise the levers on the "
                   "Breakdown page (largest zones and items) and size them in Scenarios."))
    if bud["red"]:
        r.append(t("Anticiper les mois en dépassement : lisser la production ou programmer les arrêts sur ces mois.",
                   "Prepare for the months over budget: smooth production or schedule shutdowns in those months."))
    if fc.get("available") and fc["change_pct"] > 3:
        r.append(t("La prévision est en hausse : vérifier la cause (volume, saison) avant validation du plan.",
                   "The forecast is rising: check the cause (volume, season) before approving the plan."))
    if q["rejected"]:
        r.append(t(f"Corriger et redéposer les {q['rejected']} fichiers en quarantaine (onglet Qualité / page Dépôt de données).",
                   f"Fix and re-upload the {q['rejected']} quarantined files (Quality sheet / Data intake page)."))
    if i["period"]["imputed_pct"] > 10:
        r.append(t("Réduire les jours imputés en fiabilisant la collecte quotidienne des services.",
                   "Reduce imputed days by making daily department collection more reliable."))
    if q["behind"] or q["pending"]:
        r.append(t("Relancer les services en retard pour compléter le dernier mois.",
                   "Chase the late departments to complete the latest month."))
    if fc.get("stale"):
        r.append(t("La prévision active n'est pas à jour : relancer le pipeline.", "The active forecast is outdated: rerun the pipeline."))
    if i["synthetic"]:
        r.append(t("Données de démonstration (synthétiques) : remplacer par les exports réels avant diffusion.",
                   "Demo (synthetic) data: replace with real exports before publishing."))
    return c, r
