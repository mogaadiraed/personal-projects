"""Overview – the executive one-screen summary."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from app.core import budget as bud
from app.core.i18n import tr
from app.data.loader import (
    load_active_forecast,
    load_alerts_state,
    load_config,
    load_emissions_daily,
    load_monthly_scope_summary,
)
from app.ui import charts
from app.ui import components as ui
from app.ui.format import date_label, month_long, num, pct, signed
from carbonsync.inventory import scope2


def render() -> None:
    cfg = load_config()
    df = load_emissions_daily()
    fc, run = load_active_forecast()
    if df.empty:
        ui.empty_state(tr("Aucune donnée. Lancez le pipeline (python -m carbonsync.pipeline).",
                          "No data yet. Run the pipeline (python -m carbonsync.pipeline)."))
        return

    demo = cfg.get("app", {}).get("data_mode", "synthetic") == "synthetic"
    engine = charts.MODEL_NAMES.get(run.get("model_name", ""), run.get("model_name"))
    ui.page_header(
        tr("Pilotage", "Monitor"),
        tr("Vue d'ensemble", "Overview"),
        tr("Émissions du site, tendance et trajectoire à 6 mois.", "Site emissions, trend and 6-month outlook."),
        ui.data_chips(date_label(df["date"].max()), demo, engine),
    )

    # ---- KPIs ---------------------------------------------------------------
    last12 = df.tail(365)
    prev12 = df.iloc[-730:-365] if len(df) >= 730 else last12
    total, prev_total = last12["co2e_total_t"].sum(), prev12["co2e_total_t"].sum()
    yoy = (total - prev_total) / prev_total * 100 if prev_total else 0.0
    s1, s2, s3 = (last12[c].sum() for c in ("co2e_scope1_t", "co2e_scope2_t", "co2e_other_t"))

    y, m = bud.current_period(df)
    mb = bud.evaluate_month(df, fc, cfg, y, m)
    next30 = fc.head(30)["yhat"].sum() if not fc.empty else None
    grid_factor = cfg.get("emission_factors", {}).get("electricity_kgco2_per_kwh", 0)

    ui.kpis(
        [
            ui.Kpi(tr("12 derniers mois", "Last 12 months"), num(total, 0), "tCO₂e",
                   pct(yoy, 1, signed=True), "good" if yoy <= 0 else "bad", tr("vs N-1", "vs prior year")),
            ui.Kpi(tr(f"Budget · {month_long(m)}", f"Budget · {month_long(m)}"), ui.status_label(mb.status), "",
                   signed(mb.gap, 0) + " t", "good" if mb.gap <= 0 else "bad", tr("vs plafond", "vs ceiling")),
            ui.Kpi(tr("30 prochains jours", "Next 30 days"), num(next30, 0), "tCO₂e",
                   note=tr(f"≈ {num(next30 / 30, 1)} t/jour", f"≈ {num(next30 / 30, 1)} t/day") if next30 else ""),
            ui.Kpi(tr("Part Scope 2", "Scope 2 share"), pct(s2 / total * 100 if total else 0, 0),
                   note=tr(f"Réseau {num(grid_factor, 3)} kg/kWh", f"Grid {num(grid_factor, 3)} kg/kWh")),
        ]
    )

    # ---- Scope 2 both methods, target path, alerts ------------------------------------
    s2t = scope2.totals(df, cfg, last12["date"].min(), last12["date"].max())
    bp = bud.plan(cfg, df)
    year = (df["date"].max() + pd.Timedelta(days=1)).year
    open_alerts = load_alerts_state().get("alerts", [])
    n_crit = sum(1 for a in open_alerts if a.get("severity") == "critical")
    n_open = sum(1 for a in open_alerts if a.get("severity") in ("critical", "warning"))
    ui.kpis(
        [
            ui.Kpi(tr("Scope 2 · localisation", "Scope 2 · location-based"), num(s2t["location_t"], 0), "tCO₂e",
                   note=tr("12 mois · moyenne du réseau", "12 months · grid average")),
            ui.Kpi(tr("Scope 2 · marché", "Scope 2 · market-based"), num(s2t["market_t"], 0), "tCO₂e",
                   note=tr(f"12 mois · {pct(s2t['covered_pct'], 0)} sous contrat",
                           f"12 months · {pct(s2t['covered_pct'], 0)} under contract")),
            ui.Kpi(tr(f"Plafond {year}", f"{year} cap"), num(bp.cap(year), 0), "tCO₂e",
                   note=(tr(f"trajectoire −{bp.reduction_pct:g} % en {bp.target_year}",
                            f"path to −{bp.reduction_pct:g}% by {bp.target_year}") if bp.uses_path
                         else tr("plafond fixe", "fixed cap"))),
            ui.Kpi(tr("Alertes ouvertes", "Open alerts"), str(n_open), "",
                   tr(f"{n_crit} critique(s)", f"{n_crit} critical") if n_open else "",
                   "bad" if n_crit else ("" if n_open else "good")),
        ]
    )

    # ---- Hero trajectory -----------------------------------------------------
    with ui.card(tr("Trajectoire des émissions", "Emissions trajectory")):
        window = st.segmented_control(
            "window", options=[90, 180, 365, 0], default=180, label_visibility="collapsed",
            format_func=lambda d: tr("Tout", "All") if d == 0 else tr(f"{d} j", f"{d} d"), key="ov_window",
        )
        if window is None:  # deselected; 0 means "Tout", so never use `or` here
            window = 180
        hist = df if window == 0 else df.tail(window)
        gen = cfg.get("generator", {})
        milestones = [
            (gen.get("solar_start", "2021-06-01"), tr("Solaire sur site", "On-site solar")),
            (gen.get("efficiency_start", "2023-03-01"), tr("Efficacité", "Efficiency")),
        ]
        if window == 0:
            st.caption(tr(f"Historique complet · {num(len(df), 0)} jours. Glissez le graphique ou la barre du bas "
                          "pour remonter à n'importe quelle date.",
                          f"Full history · {num(len(df), 0)} days. Drag the chart or the bar below it to go "
                          "to any date."))
        charts.show(charts.trajectory(hist, fc, milestones=milestones, scroll=window == 0,
                                      height=500 if window == 0 else 400), key=f"ov_traj_{window}")

    # ---- Scopes ---------------------------------------------------------------
    left, right = st.columns([1, 1.6])
    with left, ui.card(tr("Répartition par scope", "Scope split"), tr("12 derniers mois", "Last 12 months")):
        charts.show(charts.scope_bars(s1, s2, s3, height=280), key="ov_scopes")
    with right, ui.card(tr("Émissions mensuelles", "Monthly emissions"), tr("24 derniers mois", "Last 24 months")):
        charts.show(charts.monthly_scopes(load_monthly_scope_summary().tail(24), height=280), key="ov_monthly")
