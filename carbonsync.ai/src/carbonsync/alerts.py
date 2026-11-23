"""Alerts: what needs attention, without someone having to open every page.

Checked by the pipeline after each upload (and from the Alerts page), saved in ``data/alerts.json``:

* **budget**: the month being managed is amber or red against its ceiling (carbon budget page rule);
* **year over cap**: the full-year projection (actual + forecast + seasonal estimate, the assistant's
  ``project_year``) is above the year's cap;
* **anomaly**: actual days of the last ``anomaly_window_days`` outside the 90 % band of the forecast that was
  made *before* them (the newest run trained on data up to the day before);
* **department late**: a department's last day is behind the others; **data stale**: no new day for more than
  ``stale_after_days`` (monthly exports are expected);
* **router**: another model clearly beats the router's model in the latest backtest;
* **quarantine**: files waiting to be fixed.

Each alert keeps the date it was first seen; alerts that disappear go to a short history. ``alerts`` in
config.yaml holds the thresholds. Email is not sent (no mail server is configured).
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import yaml

from carbonsync.db import connect as db_connect
from carbonsync.obs import get_logger

log = get_logger(__name__)

SEVERITIES = ("critical", "warning", "info")
SEVERITY_LABELS = {"critical": ("Critique", "Critical"), "warning": ("À surveiller", "Warning"),
                   "info": ("Information", "Info")}
DEFAULTS: dict[str, Any] = {"stale_after_days": 45, "anomaly_window_days": 90, "router_margin_mase": 0.05}
DEPT_TABLES = {"energie": "raw_energy", "production": "raw_production",
               "logistique": "raw_logistics", "dechets_eau": "raw_waste_water"}
DEPT_NAMES = {"energie": ("Énergie", "Energy"), "production": ("Production", "Production"),
              "logistique": ("Logistique", "Logistics"), "dechets_eau": ("Déchets & eau", "Waste & water")}
STATE_FILE = "alerts.json"
HISTORY_KEEP = 200


def get_project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def settings(cfg: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict((cfg or {}).get("alerts", {}) or {})
    out = dict(DEFAULTS)
    for k in ("stale_after_days", "anomaly_window_days"):
        if raw.get(k) is not None:
            out[k] = max(1, int(raw[k]))
    if raw.get("router_margin_mase") is not None:
        out["router_margin_mase"] = max(0.0, float(raw["router_margin_mase"]))
    return out


def _alert(id_: str, kind: str, severity: str, fr: str, en: str, detail_fr: str, detail_en: str,
           page: str, **data: Any) -> dict[str, Any]:
    return {"id": id_, "kind": kind, "severity": severity, "title_fr": fr, "title_en": en,
            "detail_fr": detail_fr, "detail_en": detail_en, "page": page, "data": data}


def _fmt(v: float, lang: str = "en") -> str:
    s = f"{v:,.0f}"
    return s.replace(",", " ") if lang == "fr" else s


# ---------------------------------------------------------------------------
# Individual checks (pure functions: easy to test)
# ---------------------------------------------------------------------------
def check_budget(df: pd.DataFrame, fc: pd.DataFrame, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    from carbonsync import budget

    if df.empty:
        return []
    y, m = budget.current_period(df)
    mb = budget.evaluate_month(df, fc, cfg, y, m)
    if mb.days_covered == 0 or mb.status == "green":
        return []
    label_fr = pd.Timestamp(y, m, 1).strftime("%m/%Y")
    label_en = pd.Timestamp(y, m, 1).strftime("%b %Y")
    if mb.status == "red":
        return [_alert(f"budget:{y}-{m:02d}", "budget", "critical",
                       f"Budget {label_fr} : projection au-dessus du plafond",
                       f"Budget {label_en}: projection over the ceiling",
                       f"Projection {_fmt(mb.projected, 'fr')} t pour un plafond de {_fmt(mb.ceiling, 'fr')} t "
                       f"({mb.gap / mb.ceiling * 100:+.0f} %).",
                       f"Projected {_fmt(mb.projected)} t against a {_fmt(mb.ceiling)} t ceiling "
                       f"({mb.gap / mb.ceiling * 100:+.0f}%).", "budget",
                       year=y, month=m, projected=round(mb.projected, 1), ceiling=round(mb.ceiling, 1))]
    return [_alert(f"budget:{y}-{m:02d}", "budget", "warning",
                   f"Budget {label_fr} : la borne haute dépasse le plafond",
                   f"Budget {label_en}: the upper bound exceeds the ceiling",
                   f"Projection {_fmt(mb.projected, 'fr')} t sous le plafond de {_fmt(mb.ceiling, 'fr')} t, mais la "
                   f"borne haute 90 % atteint {_fmt(mb.upper, 'fr')} t.",
                   f"Projected {_fmt(mb.projected)} t under the {_fmt(mb.ceiling)} t ceiling, but the 90% upper "
                   f"bound reaches {_fmt(mb.upper)} t.", "budget",
                   year=y, month=m, projected=round(mb.projected, 1), upper=round(mb.upper, 1),
                   ceiling=round(mb.ceiling, 1))]


def check_year(proj: dict[str, Any] | None) -> list[dict[str, Any]]:
    """``proj``: the assistant's ``analyst.project_year`` result."""
    if not proj or proj.get("gap_t", 0) <= 0:
        return []
    y, gap, cap = proj["year"], proj["gap_t"], proj["cap"]
    sure = proj["total_lo_t"] > cap
    sev = "critical"
    certainty_fr = "même la borne basse dépasse" if sure else "le plafond est dans la fourchette 90 %"
    certainty_en = "even the low end is above" if sure else "the cap lies inside the 90% range"
    return [_alert(f"year:{y}", "year", sev, f"Fin d'année {y} au-dessus du plafond",
                   f"Year-end {y} over the cap",
                   f"Projection {_fmt(proj['total_t'], 'fr')} t pour un plafond de {_fmt(cap, 'fr')} t "
                   f"(+{_fmt(gap, 'fr')} t ; {certainty_fr}).",
                   f"Projected {_fmt(proj['total_t'])} t against a {_fmt(cap)} t cap "
                   f"(+{_fmt(gap)} t; {certainty_en}).", "budget",
                   year=y, projected=round(proj["total_t"], 1), cap=round(cap, 1),
                   low=round(proj["total_lo_t"], 1), high=round(proj["total_hi_t"], 1))]


def matched(df: pd.DataFrame, runs: pd.DataFrame, forecasts: pd.DataFrame, window_days: int) -> pd.DataFrame:
    """Each actual day of the window with the band of the newest forecast made before it."""
    cols = ["date", "actual", "yhat", "lo90", "hi90", "run_id"]
    if df.empty or runs.empty or forecasts.empty:
        return pd.DataFrame(columns=cols)
    last = df["date"].max()
    recent = df[df["date"] > last - pd.Timedelta(days=window_days)][["date", "co2e_total_t"]]
    r = runs[["run_id", "end_date", "created_at"]].copy()
    r["end_date"] = pd.to_datetime(r["end_date"])
    f = forecasts.merge(r, on="run_id")
    f["date"] = pd.to_datetime(f["date"])
    f = f[f["end_date"] < f["date"]]                      # made before the day it forecasts
    f = f.sort_values(["date", "end_date", "created_at"]).groupby("date").tail(1)
    m = recent.merge(f[["date", "yhat", "lo90", "hi90", "run_id"]], on="date")
    return m.rename(columns={"co2e_total_t": "actual"})[cols]


def anomalies(df: pd.DataFrame, runs: pd.DataFrame, forecasts: pd.DataFrame, window_days: int) -> pd.DataFrame:
    """Actual days of the window outside the 90 % band of the newest forecast made before them."""
    cols = ["date", "actual", "yhat", "lo90", "hi90", "run_id", "direction"]
    m = matched(df, runs, forecasts, window_days)
    if m.empty:
        return pd.DataFrame(columns=cols)
    out = m[(m["actual"] > m["hi90"]) | (m["actual"] < m["lo90"])].copy()
    out["direction"] = (out["actual"] > out["hi90"]).map({True: "above", False: "below"})
    return out[cols].reset_index(drop=True)


def check_anomalies(anom: pd.DataFrame, window_days: int) -> list[dict[str, Any]]:
    if anom.empty:
        return []
    n_up = int((anom["direction"] == "above").sum())
    n_down = len(anom) - n_up
    worst = anom.assign(dev=(anom["actual"] - anom["yhat"]).abs()).sort_values("dev").iloc[-1]
    first, last = anom["date"].min().date(), anom["date"].max().date()
    actual_fr, yhat_fr = (f"{worst[k]:.1f}".replace(".", ",") for k in ("actual", "yhat"))
    return [_alert(f"anomaly:{last}", "anomaly", "warning",
                   f"{len(anom)} jour(s) hors de la bande de prévision 90 %",
                   f"{len(anom)} day(s) outside the 90% forecast band",
                   f"Entre le {first} et le {last} : {n_up} au-dessus, {n_down} en dessous. Écart maximal le "
                   f"{worst['date'].date()} ({actual_fr} t pour {yhat_fr} t prévues).",
                   f"Between {first} and {last}: {n_up} above, {n_down} below. Largest on {worst['date'].date()} "
                   f"({worst['actual']:.1f} t vs {worst['yhat']:.1f} t forecast).", "alerts",
                   days=len(anom), above=n_up, below=n_down, window_days=window_days,
                   first=str(first), last=str(last))]


def check_departments(dept_last: dict[str, pd.Timestamp | None], today: date, stale_after: int) -> list[dict[str, Any]]:
    present = {d: v for d, v in dept_last.items() if v is not None}
    if not present:
        return []
    out = []
    newest = max(present.values())
    for d, v in present.items():
        lag = (newest - v).days
        if lag >= 1:
            fr, en = DEPT_NAMES.get(d, (d, d))
            out.append(_alert(f"dept:{d}", "department", "warning", f"{fr} : données en retard",
                              f"{en}: data behind",
                              f"Dernier jour reçu le {v.date()}, {lag} jour(s) après les autres services "
                              f"(jusqu'au {newest.date()}) : le dernier mois reste en attente.",
                              f"Last day received {v.date()}, {lag} day(s) behind the other departments "
                              f"(through {newest.date()}): the latest month stays waiting.", "intake",
                              department=d, last=str(v.date()), lag_days=lag))
    age = (pd.Timestamp(today) - newest).days
    if age > stale_after:
        out.append(_alert("data:stale", "stale", "warning", "Aucune nouvelle donnée depuis longtemps",
                          "No new data for a long time",
                          f"Dernier jour reçu : {newest.date()} ({age} jours). Les exports mensuels des services "
                          f"sont attendus (seuil {stale_after} jours).",
                          f"Last day received: {newest.date()} ({age} days ago). Monthly department exports are "
                          f"expected (threshold {stale_after} days).", "intake",
                          last=str(newest.date()), age_days=age, threshold_days=stale_after))
    return out


def check_router(results: list[dict[str, Any]], router_model: str, margin: float) -> list[dict[str, Any]]:
    res = pd.DataFrame(results or [])
    if res.empty or "model_name" not in res:
        return []
    means = res[res["model_name"].isin(["chronos", "ens_pc", "prophet"])].groupby("model_name")["mase"].mean()
    if means.empty or router_model not in means.index:
        return []
    best = means.idxmin()
    if best == router_model or means[router_model] - means[best] <= margin:
        return []
    names = {"chronos": "Chronos-2", "ens_pc": "Ens-PC", "prophet": "Prophet"}
    return [_alert("router", "router", "info", "Un autre modèle est plus précis que celui du routeur",
                   "Another model beats the router's model",
                   f"Dernier backtest : {names[best]} MASE {means[best]:.2f} contre {means[router_model]:.2f} pour "
                   f"{names[router_model]}. À revoir dans Configuration → Système.",
                   f"Latest backtest: {names[best]} MASE {means[best]:.2f} vs {means[router_model]:.2f} for "
                   f"{names[router_model]}. Review in Configuration → System.", "methodology",
                   best=best, router=router_model)]


def check_quarantine(files: list[str]) -> list[dict[str, Any]]:
    if not files:
        return []
    return [_alert("quarantine", "quarantine", "info", f"{len(files)} fichier(s) en quarantaine",
                   f"{len(files)} file(s) in quarantine",
                   "À corriger puis à redéposer (page Qualité des données).",
                   "To fix and upload again (Data quality page).", "data", files=files[:20])]


# ---------------------------------------------------------------------------
def evaluate(root: Path | None = None, cfg: dict[str, Any] | None = None,
             today: date | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Runs every check on the current data. Returns (alerts, facts used)."""
    from carbonsync.models.router import router_settings

    root = root or get_project_root()
    cfg = cfg if cfg is not None else yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    s = settings(cfg)
    today = today or date.today()
    db = root / cfg.get("paths", {}).get("duckdb", "data/carbonsync.duckdb")
    con = db_connect(str(db), read_only=True)
    try:
        df = con.execute("SELECT * FROM emissions_daily ORDER BY date").fetchdf()
        runs = con.execute("SELECT * FROM forecast_runs ORDER BY created_at DESC").fetchdf()
        window_start = (pd.Timestamp(df["date"].max()) - pd.Timedelta(days=s["anomaly_window_days"])) if not df.empty else None
        forecasts = con.execute("SELECT run_id, date, yhat, lo90, hi90 FROM forecasts WHERE date > ?",
                                [window_start]).fetchdf() if window_start is not None else pd.DataFrame()
        dept_last = {}
        for d, tbl in DEPT_TABLES.items():
            try:
                v = con.execute(f"SELECT MAX(date) FROM {tbl}").fetchone()[0]
            except duckdb.Error:
                v = None
            dept_last[d] = pd.Timestamp(v) if v is not None else None
    finally:
        con.close()
    df["date"] = pd.to_datetime(df["date"])

    alerts: list[dict[str, Any]] = []
    facts: dict[str, Any] = {"data_through": str(df["date"].max().date()) if not df.empty else None}
    proj = None
    fc = pd.DataFrame(columns=["date", "yhat", "lo90", "hi90"])
    try:
        from carbonsync.assistant import analyst
        from carbonsync.assistant.context import load_context

        ctx = load_context()
        fc = ctx.fc
        hs = analyst.hotspots(ctx, analyst.TREND_MONTHS)
        proj = analyst.project_year(ctx, hs)
        facts["year"] = int(proj["year"])
        facts["year_projection_t"] = round(proj["total_t"], 1)
        facts["year_projection_lo_t"] = round(proj["total_lo_t"], 1)
        facts["year_projection_hi_t"] = round(proj["total_hi_t"], 1)
        facts["year_cap_t"] = round(proj["cap"], 1)
    except Exception as err:  # noqa: BLE001 - a projection problem must not hide the other alerts
        log.warning("Alerts: year projection unavailable (%s)", err)

    alerts += check_budget(df, fc, cfg)
    alerts += check_year(proj)
    anom = anomalies(df, runs, forecasts, s["anomaly_window_days"])
    facts["anomaly_days"] = len(anom)
    alerts += check_anomalies(anom, s["anomaly_window_days"])
    alerts += check_departments(dept_last, today, s["stale_after_days"])
    bt_file = root / "data" / "backtest_summary.json"
    try:
        bt = json.loads(bt_file.read_text(encoding="utf-8")) if bt_file.exists() else {}
    except (OSError, json.JSONDecodeError):
        bt = {}
    alerts += check_router(bt.get("results", []) if isinstance(bt, dict) else [], router_settings(cfg)["model"],
                           s["router_margin_mase"])
    qdir = root / cfg.get("paths", {}).get("quarantine", "data/quarantine")
    alerts += check_quarantine(sorted(p.name.replace(".errors.json", "") for p in qdir.glob("*.errors.json")))
    order = {s_: i for i, s_ in enumerate(SEVERITIES)}
    alerts.sort(key=lambda a: order[a["severity"]])
    return alerts, {**facts, "anomalies": anom}


def band(root: Path | None = None, cfg: dict[str, Any] | None = None) -> pd.DataFrame:
    """Recent actual days with the band they were forecast in (for the Alerts page chart)."""
    root = root or get_project_root()
    cfg = cfg if cfg is not None else yaml.safe_load((root / "config.yaml").read_text(encoding="utf-8"))
    s = settings(cfg)
    db = root / cfg.get("paths", {}).get("duckdb", "data/carbonsync.duckdb")
    con = db_connect(str(db), read_only=True)
    try:
        df = con.execute("SELECT date, co2e_total_t FROM emissions_daily ORDER BY date").fetchdf()
        if df.empty:
            return matched(df, pd.DataFrame(), pd.DataFrame(), s["anomaly_window_days"])
        start = pd.Timestamp(df["date"].max()) - pd.Timedelta(days=s["anomaly_window_days"])
        runs = con.execute("SELECT run_id, end_date, created_at FROM forecast_runs").fetchdf()
        fc = con.execute("SELECT run_id, date, yhat, lo90, hi90 FROM forecasts WHERE date > ?", [start]).fetchdf()
    finally:
        con.close()
    df["date"] = pd.to_datetime(df["date"])
    return matched(df, runs, fc, s["anomaly_window_days"])


def state_path(root: Path | None = None) -> Path:
    return (root or get_project_root()) / "data" / STATE_FILE


def read_state(root: Path | None = None) -> dict[str, Any]:
    p = state_path(root)
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (OSError, json.JSONDecodeError):
        return {}


def check(root: Path | None = None, cfg: dict[str, Any] | None = None, today: date | None = None,
          trigger: str = "manual") -> dict[str, Any]:
    """Evaluates the alerts, keeps first-seen dates, moves resolved ones to the history, saves the file."""
    root = root or get_project_root()
    alerts, facts = evaluate(root, cfg, today)
    old = read_state(root)
    now = datetime.now().isoformat(timespec="seconds")
    prev = {a["id"]: a for a in old.get("alerts", [])}
    for a in alerts:
        a["first_seen"] = prev.get(a["id"], {}).get("first_seen", now)
        a["last_seen"] = now
    current = {a["id"] for a in alerts}
    history = [dict(a, resolved_at=now) for a in old.get("alerts", []) if a["id"] not in current]
    history += old.get("history", [])
    anom = facts.pop("anomalies")
    state = {"checked_at": now, "trigger": trigger, "facts": facts, "alerts": alerts,
             "anomalies": [{"date": str(r.date.date()), "actual": round(r.actual, 3), "yhat": round(r.yhat, 3),
                            "lo90": round(r.lo90, 3), "hi90": round(r.hi90, 3), "direction": r.direction,
                            "run_id": r.run_id} for r in anom.itertuples()],
             "history": history[:HISTORY_KEEP]}
    p = state_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    tmp.replace(p)
    counts = {s_: sum(1 for a in alerts if a["severity"] == s_) for s_ in SEVERITIES}
    log.info("Alerts checked (%s): %d critical, %d warning, %d info", trigger, counts["critical"],
             counts["warning"], counts["info"])
    return state


def counts(state: dict[str, Any]) -> dict[str, int]:
    return {s_: sum(1 for a in state.get("alerts", []) if a.get("severity") == s_) for s_ in SEVERITIES}


if __name__ == "__main__":
    from carbonsync.obs import setup_console_logging

    setup_console_logging()
    st = check(trigger="cli")
    for a in st["alerts"]:
        print(f"[{a['severity']}] {a['title_en']} - {a['detail_en']}")
