"""Breakdown – emissions by plant zone (P1 cutting, P2 preparation, P3 assembly, utilities, logistics) and item.

Each site meter is split between items with the allocation keys of config.yaml (``breakdown``), so every
reduction lever has a zone and an owner. The keys are estimates until sub-meters exist.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from app.core import auth, nav
from app.core.i18n import lang, tr
from app.data.loader import load_config, load_emissions_daily
from app.ui import charts, inv_charts
from app.ui import components as ui
from app.ui.format import date_label, num, pct
from carbonsync.inventory import breakdown as bd

SCOPE_LABELS = {"scope1": "Scope 1", "scope2": "Scope 2", "other": ("Autre", "Other")}


def period_picker(df: pd.DataFrame, key: str) -> tuple[pd.Timestamp, pd.Timestamp, str]:
    """Last 12 months, or one calendar year of the data. Returns (start, end, label)."""
    last = df["date"].max()
    years = sorted({int(y) for y in df["date"].dt.year}, reverse=True)
    opts = ["12m"] + [str(y) for y in years]
    choice = st.segmented_control(tr("Période", "Period"), opts, default="12m", key=key, label_visibility="collapsed",
                                  format_func=lambda o: tr("12 derniers mois", "Last 12 months") if o == "12m" else o)
    choice = choice or "12m"
    if choice == "12m":
        start = last - pd.Timedelta(days=364)
        return start, last, tr(f"{date_label(start)} – {date_label(last)}", f"{date_label(start)} – {date_label(last)}")
    y = int(choice)
    start, end = pd.Timestamp(y, 1, 1), min(pd.Timestamp(y, 12, 31), last)
    partial = end < pd.Timestamp(y, 12, 31)
    return start, end, str(y) + (tr(" (partielle)", " (partial)") if partial else "")


def render() -> None:
    cfg = load_config()
    df = load_emissions_daily()
    if df.empty:
        ui.empty_state(tr("Aucune donnée d'émissions.", "No emissions data."))
        return
    L = lang()
    demo = cfg.get("app", {}).get("data_mode", "synthetic") == "synthetic"
    ui.page_header(
        tr("Pilotage", "Monitor"), tr("Répartition", "Breakdown"),
        tr("Qui émet quoi : chaque compteur du site réparti entre zones et postes (clés de répartition).",
           "Who emits what: each site meter split between zones and items (allocation keys)."),
        ui.data_chips(date_label(df["date"].max()), demo) + [(tr("Clés estimées", "Estimated keys"), "warn")],
    )
    start, end, label = period_picker(df, "bk_period")
    s = bd.settings(cfg)
    errors = bd.validate(s)
    if errors:
        st.warning(tr("Clés de répartition à corriger (le total ne correspond plus au site) : ",
                      "Allocation keys to fix (the total no longer matches the site): ") + "; ".join(errors))

    zones = bd.by_zone(df, cfg, start, end)
    items = bd.by_item(df, cfg, start, end)
    if zones.empty:
        ui.empty_state(tr("Aucune donnée sur cette période.", "No data for this period."))
        return
    total = float(zones["t"].sum())
    top_z, top_i = zones.iloc[0], items.iloc[0]
    ui.kpis([
        ui.Kpi(tr("Total réparti", "Total allocated"), num(total, 0), "tCO₂e", note=label),
        ui.Kpi(tr("Première zone", "Largest zone"), str(top_z[f"zone_{L}"]), "",
               note=f"{pct(top_z['share_pct'], 0)} · {top_z['owner']}"),
        ui.Kpi(tr("Premier poste", "Largest item"), str(top_i[f"item_{L}"]), "",
               note=f"{num(top_i['t'], 0)} t · {pct(top_i['share_pct'], 0)}"),
        ui.Kpi(tr("Postes suivis", "Items tracked"), str(len(items)), "", note=tr(f"{len(zones)} zones", f"{len(zones)} zones")),
    ])

    left, right = st.columns([1, 1.25])
    with left, ui.card(tr("Par zone", "By zone"), label):
        charts.show(inv_charts.zone_bars(zones, f"zone_{L}", height=320), key="bk_zones")
    with right, ui.card(tr("Principaux postes", "Largest items"), tr("Couleur = zone", "Colour = zone")):
        charts.show(inv_charts.items_bars(items, f"item_{L}", f"zone_{L}", top=10, height=320), key="bk_items")

    with ui.card(tr("Évolution mensuelle par zone", "Monthly trend by zone"), tr("24 derniers mois", "Last 24 months")):
        m_start = (df["date"].max() - pd.DateOffset(months=23)).to_period("M").to_timestamp()
        alloc = bd.allocate(df, cfg, m_start, df["date"].max(), freq="M")
        charts.show(inv_charts.zone_monthly(alloc, f"zone_{L}"), key="bk_monthly")

    with ui.card(tr("Détail par poste", "Detail by item"), label):
        table = pd.DataFrame({
            tr("Poste", "Item"): items[f"item_{L}"],
            tr("Zone", "Zone"): items[f"zone_{L}"],
            tr("Responsable", "Owner"): items["owner"],
            tr("Source", "Source"): items[f"source_{L}"],
            "Scope": [tr(*v) if isinstance(v, tuple) else v for v in items["scope"].map(SCOPE_LABELS)],
            "tCO₂e": items["t"].round(1),
            tr("Part", "Share"): items["share_pct"].round(1),
        })
        st.dataframe(table, hide_index=True, width="stretch", column_config={
            tr("Part", "Share"): st.column_config.ProgressColumn(format="%.1f %%", min_value=0, max_value=float(items["share_pct"].max() or 1)),
            "tCO₂e": st.column_config.NumberColumn(format="%.1f")})

    st.caption(tr("Les clés de répartition (config.yaml → breakdown) sont des estimations : elles répartissent chaque "
                  "compteur du site entre les postes. Les remplacer par des relevés de sous-compteurs dès qu'ils "
                  "existent. Le total est toujours égal au total du site (Scope 1 + Scope 2 localisation + déchets et eau).",
                  "The allocation keys (config.yaml → breakdown) are estimates: they split each site meter between "
                  "items. Replace them with sub-meter readings as soon as they exist. The total always equals the "
                  "site total (Scope 1 + Scope 2 location-based + waste and water)."))
    if auth.can("settings_inventory"):
        nav.link("configuration", tr("Modifier les clés (Configuration → Inventaire et répartition)",
                                     "Edit the keys (Configuration → Inventory & breakdown)"), icon=":material/edit:")
