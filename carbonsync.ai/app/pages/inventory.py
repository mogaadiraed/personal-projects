"""GHG inventory – Scopes 1, 2 and 3 as ESRS E1-6 asks: Scope 2 by both methods, Scope 3 screening.

* Scope 2 location-based (grid average) and market-based (contracts + residual mix), side by side.
* Scope 3: the 15 GHG Protocol categories, each calculated, estimated or excluded, with method and source.
The budget perimeter (Scope 1 + Scope 2 location-based + waste and water) is unchanged; this page adds the
rest of the inventory around it.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from app.core import auth, nav
from app.core.i18n import lang, tr
from app.data.loader import load_config, load_emissions_daily
from app.pages.breakdown import period_picker
from app.ui import charts, inv_charts
from app.ui import components as ui
from app.ui.format import date_label, num, pct
from carbonsync.inventory import scope2, scope3

STATUS = {"calculated": ("Calculé", "Calculated"), "estimated": ("Estimé", "Estimated"), "excluded": ("Exclu", "Excluded")}
QUALITY = {"high": ("Élevée", "High"), "medium": ("Moyenne", "Medium"), "low": ("Faible", "Low"), "—": ("—", "—")}


def render() -> None:
    cfg = load_config()
    df = load_emissions_daily()
    if df.empty:
        ui.empty_state(tr("Aucune donnée d'émissions.", "No emissions data."))
        return
    L = lang()
    demo = cfg.get("app", {}).get("data_mode", "synthetic") == "synthetic"
    ui.page_header(
        tr("Preuves", "Evidence"), tr("Inventaire GES", "GHG inventory"),
        tr("Scopes 1, 2 et 3 comme le demande l'ESRS E1-6 : Scope 2 selon les deux méthodes, présélection du Scope 3.",
           "Scopes 1, 2 and 3 as ESRS E1-6 asks: Scope 2 by both methods, Scope 3 screening."),
        ui.data_chips(date_label(df["date"].max()), demo),
    )
    start, end, label = period_picker(df, "inv_period")
    part = df[(df["date"] >= start) & (df["date"] <= end)]
    s1 = float(part["co2e_scope1_t"].sum())
    s2 = scope2.totals(df, cfg, start, end)
    s3 = scope3.summary(df, cfg, start, end)
    rows = scope3.screening(df, cfg, start, end)

    ui.kpis([
        ui.Kpi("Scope 1", num(s1, 0), "tCO₂e", note=tr("fioul, gazole", "fuel oil, diesel")),
        ui.Kpi(tr("Scope 2 · localisation", "Scope 2 · location-based"), num(s2["location_t"], 0), "tCO₂e",
               note=tr(f"réseau {num(s2['grid_kgco2_per_kwh'], 3)} kg/kWh", f"grid {num(s2['grid_kgco2_per_kwh'], 3)} kg/kWh")),
        ui.Kpi(tr("Scope 2 · marché", "Scope 2 · market-based"), num(s2["market_t"], 0), "tCO₂e",
               note=tr(f"{pct(s2['covered_pct'], 0)} sous contrat", f"{pct(s2['covered_pct'], 0)} under contract")),
        ui.Kpi(tr("Scope 3 · présélection", "Scope 3 · screening"), num(s3["total_t"], 0), "tCO₂e",
               note=tr(f"{num(s3['estimated_t'], 0)} t estimées", f"{num(s3['estimated_t'], 0)} t estimated")),
    ])
    st.caption(scope2.note(s2, L))

    with ui.card(tr("Tableau ESRS E1-6 (émissions brutes)", "ESRS E1-6 table (gross emissions)"), label):
        total_loc = s1 + s2["location_t"] + s3["total_t"]
        total_mkt = s1 + s2["market_t"] + s3["total_t"]
        esrs = pd.DataFrame([
            (tr("Scope 1 brut", "Gross Scope 1"), s1, tr("Mesuré (compteurs, factures)", "Measured (meters, invoices)")),
            (tr("Scope 2 brut – méthode localisation", "Gross Scope 2 – location-based"), s2["location_t"],
             tr("Mesuré × facteur moyen du réseau", "Measured × grid average factor")),
            (tr("Scope 2 brut – méthode marché", "Gross Scope 2 – market-based"), s2["market_t"],
             tr("Instruments contractuels + mix résiduel", "Contractual instruments + residual mix")),
            (tr("Scope 3 brut (présélection)", "Gross Scope 3 (screening)"), s3["total_t"],
             tr(f"Calculé {num(s3['calculated_t'], 0)} t · estimé {num(s3['estimated_t'], 0)} t",
                f"Calculated {num(s3['calculated_t'], 0)} t · estimated {num(s3['estimated_t'], 0)} t")),
            (tr("Total (méthode localisation)", "Total (location-based)"), total_loc, "S1 + S2 loc. + S3"),
            (tr("Total (méthode marché)", "Total (market-based)"), total_mkt, "S1 + S2 " + tr("marché", "market") + " + S3"),
        ], columns=[tr("Poste", "Line"), "tCO₂e", tr("Méthode", "Method")])
        st.dataframe(esrs, hide_index=True, width="stretch",
                     column_config={"tCO₂e": st.column_config.NumberColumn(format="%.1f")})
        st.caption(tr("Déchets et eau sont comptés en Scope 3 (catégories 5 et 1) dans l'inventaire ; ils font partie "
                      "du total du budget (« autres »), qui garde son périmètre : Scope 1 + Scope 2 localisation + "
                      "déchets et eau.",
                      "Waste and water are counted in Scope 3 (categories 5 and 1) in the inventory; they are part of "
                      "the budget total (\"other\"), which keeps its perimeter: Scope 1 + Scope 2 location-based + waste "
                      "and water."))

    left, right = st.columns([1.2, 1])
    with left, ui.card(tr("Scope 2 par année, deux méthodes", "Scope 2 by year, both methods")):
        years = sorted({int(y) for y in df["date"].dt.year})
        ann = pd.DataFrame([{"year": y, **{k: scope2.totals(df, cfg, f"{y}-01-01", f"{y}-12-31")[k]
                                           for k in ("location_t", "market_t")}} for y in years])
        charts.show(inv_charts.scope2_years(ann), key="inv_s2")
    with right, ui.card(tr("Instruments contractuels", "Contractual instruments"),
                        tr("PPA, offre verte, certificats (config.yaml → scope2)", "PPA, green tariff, certificates (config.yaml → scope2)")):
        st2 = scope2.settings(cfg)
        if st2["instruments"]:
            st.dataframe(pd.DataFrame([{
                tr("Nom", "Name"): i["name"], tr("Type", "Kind"): tr(*scope2.KIND_LABELS[i["kind"]]),
                tr("Début", "Start"): i["start"].date() if i["start"] is not None else None,
                tr("Fin", "End"): i["end"].date() if i["end"] is not None else None,
                tr("Couverture", "Coverage"): (pct(i["share_of_grid_kwh"] * 100, 0) if i["share_of_grid_kwh"] is not None
                                               else f"{num(i['mwh_per_year'], 0)} MWh/" + tr("an", "yr")),
                "kgCO₂e/kWh": i["kgco2_per_kwh"], tr("Actif", "On"): i["enabled"],
            } for i in st2["instruments"]]), hide_index=True, width="stretch")
        else:
            st.caption(tr("Aucun instrument enregistré.", "No instrument recorded."))
        st.caption(tr(f"Mix résiduel : {num(st2['residual_mix_kgco2_per_kwh'], 3)} kgCO₂e/kWh. ",
                      f"Residual mix: {num(st2['residual_mix_kgco2_per_kwh'], 3)} kgCO₂e/kWh. ") + st2["residual_mix_source"])

    with ui.card(tr("Scope 3 – présélection des 15 catégories", "Scope 3 – screening of the 15 categories"), label):
        c1, c2 = st.columns([1, 1.5])
        with c1:
            by_cat = pd.DataFrame(s3["by_category"])
            if not by_cat.empty:
                charts.show(inv_charts.scope3_bars(by_cat, f"category_{L}"), key="inv_s3")
        with c2:
            st.dataframe(pd.DataFrame({
                tr("Cat.", "Cat."): rows["category"],
                tr("Catégorie", "Category"): rows[f"category_{L}"],
                tr("Poste", "Item"): rows[f"item_{L}"],
                tr("Statut", "Status"): [tr(*STATUS[x]) for x in rows["status"]],
                "tCO₂e": rows["t"],
                tr("Qualité", "Quality"): [tr(*QUALITY.get(x, (x, x))) for x in rows["quality"]],
                tr("Méthode", "Method"): rows[f"method_{L}"],
                tr("Source ou motif", "Source or reason"): rows["source"],
            }), hide_index=True, width="stretch", height=420,
                column_config={"tCO₂e": st.column_config.NumberColumn(format="%.1f")})
        st.caption(tr("Calculé : données du site. Estimé : hypothèses sourcées (matières par unité produite, distances, "
                      "trajets du personnel), à remplacer par les achats réels et une enquête déplacements. Exclu : "
                      "non applicable ou faible, avec le motif. Ordres de grandeur pour prioriser, pas des chiffres audités.",
                      "Calculated: site data. Estimated: sourced assumptions (materials per unit produced, distances, "
                      "staff commuting), to replace with actual purchases and a commuting survey. Excluded: not "
                      "applicable or small, with the reason. Orders of magnitude to set priorities, not audited figures."))
    if auth.can("settings_inventory"):
        nav.link("configuration", tr("Modifier les hypothèses (Configuration → Inventaire et répartition)",
                                     "Edit the assumptions (Configuration → Inventory & breakdown)"), icon=":material/edit:")
