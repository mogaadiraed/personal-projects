"""Data quality – ingestion health, coverage and quarantine log."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from app.core.i18n import tr
from app.data.loader import (
    load_config,
    load_daily_raw_features,
    load_data_quality_report,
    load_quarantine_details,
)
from app.ui import charts
from app.ui import components as ui
from app.ui.format import date_label, num, pct


def render() -> None:
    cfg = load_config()
    dq = load_data_quality_report()
    df = load_daily_raw_features()
    quarantine = load_quarantine_details()

    scanned = int(dq.get("total_files_scanned", 0))
    accepted = int(dq.get("accepted_count", 0))
    rejected = int(dq.get("rejected_count", len(quarantine)))
    rate = accepted / scanned * 100 if scanned else 0.0
    days = len(df)
    imputed = int(df["is_imputed"].sum()) if "is_imputed" in df else 0
    imp_pct = imputed / days * 100 if days else 0.0
    score = max(0.0, 100 - (100 - rate) * 1.5 - imp_pct * 0.5)

    demo = cfg.get("app", {}).get("data_mode", "synthetic") == "synthetic"
    ui.page_header(
        tr("Preuves", "Evidence"),
        tr("Qualité des données", "Data quality"),
        tr("Ce qui est entré, ce qui a été corrigé, ce qui a été rejeté.", "What came in, what was fixed, what was rejected."),
        ui.data_chips(date_label(df["date"].max()) if days else "—", demo),
    )

    ui.steps(
        [
            (tr("Dépôt", "Inbox"), tr(f"{scanned} fichiers CSV", f"{scanned} CSV files")),
            (tr("Validation", "Validation"), tr("Schémas Pandera", "Pandera schemas")),
            (tr("Correction", "Repair"), tr("Unités, séparateurs, alias", "Units, delimiters, aliases")),
            (tr("Consolidation", "Merge"), tr(f"{num(days, 0)} jours DuckDB", f"{num(days, 0)} days in DuckDB")),
            (tr("Prévision", "Forecast"), "Prophet · Chronos-2 · Ens-PC"),
        ]
    )

    ui.kpis(
        [
            ui.Kpi(tr("Fichiers acceptés", "Files accepted"), f"{accepted}/{scanned}", "", pct(rate, 1),
                   "good" if rate >= 95 else "bad"),
            ui.Kpi(tr("En quarantaine", "Quarantined"), str(rejected), tr("fichiers", "files")),
            ui.Kpi(tr("Jours imputés", "Imputed days"), num(imputed, 0), "", note=pct(imp_pct, 1) + tr(" de l'historique", " of history")),
            ui.Kpi(tr("Indice de confiance", "Confidence score"), num(score, 0), "/ 100",
                   tr("Élevé", "High") if score >= 90 else tr("Modéré", "Moderate"), "good" if score >= 90 else "bad"),
        ]
    )

    if days:
        with ui.card(
            tr("Couverture journalière", "Daily coverage"),
            tr("Vert : observé · Orange : imputé (≤ 3 j) · Gris : absent", "Green: observed · Orange: imputed (≤ 3 d) · Gray: missing"),
        ):
            charts.show(charts.coverage_heatmap(df), key="dq_heat")

    left, right = st.columns([1, 1.5])
    with left, ui.card(tr("Motifs de rejet", "Rejection reasons")):
        if quarantine:
            charts.show(charts.rejection_bars(quarantine), key="dq_rej")
        else:
            st.success(tr("Aucun fichier rejeté.", "No rejected files."))

    with right, ui.card(tr("Journal de quarantaine", "Quarantine log")):
        depts = sorted({q.get("department", "") for q in quarantine if q.get("department")})
        pick = st.pills("dept", depts, selection_mode="multi", label_visibility="collapsed", key="dq_dept",
                        format_func=lambda d: d.replace("_", " ").title())
        rows = [
            {
                tr("Fichier", "File"): q.get("file_name", ""),
                tr("Département", "Department"): q.get("department", "").replace("_", " ").title(),
                tr("Problème", "Issue"): " · ".join(map(str, q.get("errors", []))),
                tr("Correction", "Fix"): q.get("suggested_fix", ""),
            }
            for q in quarantine
            if not pick or q.get("department") in pick
        ]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=290)

    with st.expander(tr("Schémas attendus", "Expected schemas")):
        st.dataframe(
            pd.DataFrame(
                [
                    ("energie_YYYY-MM.csv", "date, electricity_kwh, fuel_oil_l"),
                    ("production_YYYY-MM.csv", "date, units_produced, operating_hours, shutdown_flag"),
                    ("logistique_YYYY-MM.csv", "date, trips, diesel_l"),
                    ("dechets_eau_YYYY-MM.csv", "date, waste_kg, water_m3"),
                ],
                columns=[tr("Fichier", "File"), tr("Colonnes", "Columns")],
            ),
            hide_index=True, width="stretch",
        )
