"""Carbon budget – monthly ceiling tracking, year view and the path to the reduction target.

Caps come from the budget plan (``carbonsync.targets``): each year's cap is read on the straight-line path
from the base year to the target, and split by month with the site's seasonal profile.
"""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from app.core import auth, nav
from app.core import budget as bud
from app.core.i18n import lang, tr
from app.data.loader import (
    load_active_forecast,
    load_config,
    load_emissions_daily,
    load_year_projection,
)
from app.ui import charts, inv_charts
from app.ui import components as ui
from app.ui.format import date_label, month_long, num, pct, signed


def render() -> None:
    cfg = load_config()
    df = load_emissions_daily()
    fc, _ = load_active_forecast()
    if df.empty:
        ui.empty_state(tr("Aucune donnée d'émissions.", "No emissions data."))
        return

    demo = cfg.get("app", {}).get("data_mode", "synthetic") == "synthetic"
    bp = bud.plan(cfg, df)
    ui.page_header(
        tr("Planification", "Plan"),
        tr("Budget carbone", "Carbon budget"),
        tr("Réel + prévision comparés au plafond mensuel, lu sur la trajectoire vers l'objectif.",
           "Actuals + forecast against the monthly ceiling, read on the path to the target."),
        ui.data_chips(date_label(df["date"].max()), demo)
        + [(tr(f"Objectif −{bp.reduction_pct:g} % en {bp.target_year}", f"Target −{bp.reduction_pct:g}% by {bp.target_year}"), "accent")
           if bp.uses_path else (tr("Plafond fixe", "Fixed cap"), "")],
    )
    for w in bp.warnings:
        st.warning(w)

    # ---- Period selector -------------------------------------------------------
    cur_y, cur_m = bud.current_period(df)
    years = sorted(set(df["date"].dt.year) | (set(fc["date"].dt.year) if not fc.empty else set()))
    c1, c2, _ = st.columns([1, 1.4, 3])
    year = c1.selectbox(tr("Année", "Year"), years, index=years.index(cur_y), key="bd_year")
    month = c2.selectbox(
        tr("Mois", "Month"), list(range(1, 13)), index=cur_m - 1, format_func=month_long, key="bd_month"
    )

    mb = bud.evaluate_month(df, fc, cfg, year, month)
    msg = {
        "green": tr(
            f"Même la borne haute ({num(mb.upper, 0)} t) reste sous le plafond de {num(mb.ceiling, 0)} t.",
            f"Even the upper bound ({num(mb.upper, 0)} t) stays below the {num(mb.ceiling, 0)} t ceiling.",
        ),
        "amber": tr(
            f"Projection médiane conforme, mais la borne haute dépasse de {num(mb.upper - mb.ceiling, 0)} t.",
            f"Median projection fits, but the upper bound exceeds it by {num(mb.upper - mb.ceiling, 0)} t.",
        ),
        "red": tr(
            f"Projection au-dessus du plafond de {num(mb.gap, 0)} t ({pct(mb.gap / mb.ceiling * 100, 1, True)}).",
            f"Projection is {num(mb.gap, 0)} t over the ceiling ({pct(mb.gap / mb.ceiling * 100, 1, True)}).",
        ),
    }[mb.status]
    ui.status_banner(mb.status, msg)

    ui.kpis(
        [
            ui.Kpi(tr("Plafond du mois", "Monthly ceiling"), num(mb.ceiling, 0), "tCO₂e",
                   note=tr(f"Poids {pct(mb.weight * 100, 1)}", f"Weight {pct(mb.weight * 100, 1)}")),
            ui.Kpi(tr("Consommé", "Spent"), num(mb.spent, 0), "tCO₂e",
                   note=tr(f"{mb.days_observed}/{mb.days_in_month} jours observés", f"{mb.days_observed}/{mb.days_in_month} days observed")),
            ui.Kpi(tr("Projection fin de mois", "Projected month-end"), num(mb.projected, 0), "tCO₂e",
                   signed(mb.gap, 0) + " t", "good" if mb.gap <= 0 else "bad"),
            ui.Kpi(tr("Borne haute 90 %", "90% upper bound"), num(mb.upper, 0), "tCO₂e"),
        ]
    )

    left, right = st.columns([1.5, 1])
    with left, ui.card(tr("Consommation cumulée du mois", "Cumulative consumption this month")):
        charts.show(charts.burndown(mb), key="bd_burn")

    with right, ui.card(tr(f"Année {year}", f"Year {year}")):
        ys = bud.year_summary(df, fc, cfg, year)
        cap = bp.cap(year)
        used = ys["actual"].sum()
        st.progress(min(1.0, used / cap) if cap else 0.0,
                    text=tr(f"{num(used, 0)} / {num(cap, 0)} t consommés", f"{num(used, 0)} / {num(cap, 0)} t used"))
        if ys["covered"].all():
            expected = used + ys["forecast"].sum()
            st.caption(tr(f"Atterrissage estimé : {num(expected, 0)} t ({pct(expected / cap * 100, 0)} du plafond)",
                          f"Expected landing: {num(expected, 0)} t ({pct(expected / cap * 100, 0)} of cap)"))
        else:
            last = month_long(int(ys.loc[ys["covered"], "month"].max())) if ys["covered"].any() else "—"
            st.caption(tr(f"Prévision disponible jusqu'en {last.lower()} — mois suivants non estimés.",
                          f"Forecast covers up to {last} — later months not estimated."))
        charts.show(charts.budget_year(ys, height=290), key="bd_year_chart")

    _path_section(cfg, df, bp)


def _path_section(cfg: dict, df: pd.DataFrame, bp) -> None:
    ui.section(tr("Trajectoire vers l'objectif", "Path to the target"), bp.describe(lang()))
    key = json.dumps({"t": cfg.get("targets"), "b": cfg.get("budget")}, sort_keys=True, default=str)
    try:
        proj = load_year_projection(key)
    except Exception:  # noqa: BLE001 - the path is still useful without the projection
        proj = None
    left, right = st.columns([1.5, 1])
    with left, ui.card(tr("Émissions annuelles et trajectoire", "Annual emissions and path"),
                       tr("Barres : réel (années complètes) · ligne : plafond de chaque année",
                          "Bars: actual (complete years) · line: each year's cap")):
        path = bp.path_table(until=proj["year"] if proj else None)
        projected = None
        if proj and bp.uses_path:
            projected = {"year": proj["year"], "total": proj["total"], "lo": proj["lo"], "hi": proj["hi"]}
        charts.show(inv_charts.path_chart(path, projected), key="bd_path")
        if proj:
            gap = proj["total"] - proj["cap"]
            st.caption(tr(f"{proj['year']} : projection {num(proj['total'], 0)} t (90 % : {num(proj['lo'], 0)}–{num(proj['hi'], 0)}) "
                          f"pour un plafond de {num(proj['cap'], 0)} t, écart {signed(gap, 0)} t.",
                          f"{proj['year']}: projected {num(proj['total'], 0)} t (90%: {num(proj['lo'], 0)}–{num(proj['hi'], 0)}) "
                          f"against a {num(proj['cap'], 0)} t cap, gap {signed(gap, 0)} t."))
    with right, ui.card(tr("Poids mensuels", "Monthly weights"),
                        tr(f"Profil saisonnier moyen {', '.join(map(str, bp.weights_years))}",
                           f"Average seasonal profile {', '.join(map(str, bp.weights_years))}")
                        if bp.weights_source == "seasonal" and bp.weights_years else tr("Saisis à la main", "Entered by hand")):
        charts.show(inv_charts.weights_chart(bp.weights, height=260), key="bd_weights")
        st.caption(tr("Ligne pointillée : répartition égale (1/12). Le plafond d'un mois = plafond de l'année × son poids.",
                      "Dotted line: equal split (1/12). A month's ceiling = the year's cap × its weight."))
    with st.expander(tr("Tableau de la trajectoire", "Path table")):
        t = bp.path_table(until=proj["year"] if proj else None)
        st.dataframe(pd.DataFrame({
            tr("Année", "Year"): t["year"].astype(str), tr("Plafond (t)", "Cap (t)"): t["cap"].round(1),
            tr("Réel (t)", "Actual (t)"): t["actual"].round(1), tr("Écart (t)", "Gap (t)"): t["gap"].round(1),
            tr("Sur la trajectoire", "On the path"): t["on_track"].map({True: "✓", False: "✗"}).fillna("—"),
        }), hide_index=True, width="stretch")
    if auth.can("settings_targets"):
        nav.link("configuration", tr("Modifier l'objectif et le budget (Configuration → Objectif et budget)",
                                     "Edit the target and budget (Configuration → Target & budget)"), icon=":material/edit:")
