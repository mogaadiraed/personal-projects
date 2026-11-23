"""Reports – conclusions at a glance, full Excel audit workbook, 2-page PDF brief, dataset exports."""
from __future__ import annotations

import json
from datetime import datetime
from html import escape
from pathlib import Path

import pandas as pd
import streamlit as st

from app.core.i18n import lang, tr
from app.data.loader import (
    get_project_root,
    load_active_forecast,
    load_backtest_summary,
    load_config,
    load_emissions_daily,
)
from app.ui import appearance as ap
from app.ui import components as ui
from app.ui.format import date_label, num, pct

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
KINDS = {
    "xlsx": ("CarbonSync_Report_*.xlsx", XLSX),
    "pdf": ("CarbonSync_Brief_*.pdf", "application/pdf"),
}


def _report_lang() -> str:
    """Language of the Excel / PDF documents (Settings → Reports), the interface language by default."""
    chosen = ap.current()["reports"]["language"]
    return lang() if chosen == "ui" else chosen


@st.cache_data(show_spinner=False, ttl=300)
def _insights(language: str, run_id: str | None, _stamp: float) -> dict:
    from carbonsync.export.insights import build_insights

    return build_insights(language, run_id=run_id)


def _reports_dir() -> Path:
    d = get_project_root() / "data" / "reports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _latest(kind: str) -> list[Path]:
    return sorted(_reports_dir().glob(KINDS[kind][0]), key=lambda p: p.stat().st_mtime, reverse=True)


def render() -> None:
    cfg = load_config()
    df = load_emissions_daily()
    fc, run = load_active_forecast()
    bt = load_backtest_summary()
    demo = cfg.get("app", {}).get("data_mode", "synthetic") == "synthetic"

    ui.page_header(
        tr("Preuves", "Evidence"),
        tr("Rapports", "Reports"),
        tr("Conclusions, classeur d'audit CSRD / ESRS E1, rapport de synthèse PDF et exports de données.",
           "Conclusions, CSRD / ESRS E1 audit workbook, PDF summary report and data exports."),
        ui.data_chips(date_label(df["date"].max()) if not df.empty else "—", demo),
    )
    if df.empty:
        ui.empty_state(tr("Aucune donnée.", "No data."))
        return

    run_id = str(run["run_id"]) if run else None
    stamp = float(df["date"].max().value) + len(df)
    try:
        ins = _insights(lang(), run_id, stamp)
    except Exception as exc:  # noqa: BLE001 - the rest of the page still works
        st.error(f"{type(exc).__name__}: {exc}")
        ins = None

    if ins:
        _summary(ins)
    _documents(run_id)
    _datasets(df, fc, bt, ins)
    _esrs()


# ---------------------------------------------------------------------------
def _summary(ins: dict) -> None:
    tot, fcst, bud, q = ins["totals"], ins["forecast"], ins["budget"], ins["quality"]
    yoy = tot["yoy_pct"]
    ui.kpis([
        ui.Kpi(tr("12 derniers mois", "Last 12 months"), num(tot["last12"], 0), "tCO₂e",
               pct(yoy, 1, signed=True) if pd.notna(yoy) else "", "bad" if pd.notna(yoy) and yoy > 0 else "good",
               tr("vs 12 mois précédents", "vs previous 12 months")),
        ui.Kpi(tr("Intensité carbone", "Carbon intensity"), num(tot["intensity_12"], 1), tr("kg/unité", "kg/unit"),
               note=tr("12 derniers mois", "last 12 months")),
        ui.Kpi(tr(f"Prévision {fcst.get('days', 0)} j", f"{fcst.get('days', 0)}-day forecast"),
               num(fcst.get("total"), 0) if fcst.get("available") else "—", "tCO₂e",
               note=fcst.get("model_name", "")),
        ui.Kpi(tr(f"Budget {bud['year']}", f"{bud['year']} budget"), num(bud["actual_ytd"], 0), f"/ {num(bud['cap'], 0)} t",
               note=tr(f"{len(bud['red'])} mois en dépassement", f"{len(bud['red'])} months over")),
    ])
    left, right = st.columns(2, gap="medium")
    with left, ui.card(tr("Conclusions", "Conclusions"), stretch=True,
                       subtitle=tr("Générées automatiquement à partir des données et de la prévision active.",
                          "Generated automatically from the data and the active forecast.")):
        st.html("<ol class='cs-list'>" + "".join(f"<li>{escape(c)}</li>" for c in ins["conclusions"]) + "</ol>")
    with right, ui.card(tr("Recommandations", "Recommendations"), stretch=True):
        st.html("<ul class='cs-list arrow'>" + "".join(f"<li>{escape(r)}</li>" for r in ins["recommendations"]) + "</ul>")
        st.caption(tr(f"Qualité : {q['accepted']}/{q['scanned']} fichiers acceptés · {q['rejected']} en quarantaine.",
                      f"Quality: {q['accepted']}/{q['scanned']} files accepted · {q['rejected']} quarantined."))


def _documents(run_id: str | None) -> None:
    c1, c2 = st.columns(2, gap="medium")
    specs = [
        (c1, "xlsx", tr("Classeur d'audit complet", "Full audit workbook"),
         tr("Excel · 10 onglets : synthèse, annuel, mensuel, activité, prévision, budget, modèles, qualité, méthodologie, journal",
            "Excel · 10 sheets: summary, annual, monthly, activity, forecast, budget, models, quality, methodology, log"),
         ":material/table_view:"),
        (c2, "pdf", tr("Rapport de synthèse", "Summary report"),
         tr("PDF · 3 pages : chiffres clés, conclusions, recommandations, budget, prévision, modèles, inventaire GES, "
            "trajectoire, répartition, alertes",
            "PDF · 3 pages: key figures, conclusions, recommendations, budget, forecast, models, GHG inventory, path, "
            "breakdown, alerts"),
         ":material/picture_as_pdf:"),
    ]
    for col, kind, title, sub, icon in specs:
        with col, ui.card(title, sub, stretch=True):
            a, b = st.columns(2)
            from app.core import auth

            if a.button(tr("Générer", "Generate"), type="primary", icon=icon, width="stretch", key=f"gen_{kind}",
                        disabled=not auth.can("reports_generate"),
                        help=None if auth.can("reports_generate") else auth.need("reports_generate")):
                with st.spinner(tr("Génération…", "Generating…")):
                    try:
                        if kind == "xlsx":
                            from carbonsync.export.report import generate_excel_report

                            path = generate_excel_report(lang=_report_lang(), run_id=run_id)
                        else:
                            from carbonsync.export.brief import generate_pdf_brief

                            path = generate_pdf_brief(lang=_report_lang(), run_id=run_id)
                    except Exception as exc:  # noqa: BLE001
                        st.error(f"{type(exc).__name__}: {exc}")
                        path = None
                if path:
                    st.toast(tr(f"Créé : {path.name}", f"Created: {path.name}"), icon=":material/check:")
            files = _latest(kind)
            if files:
                latest = files[0]
                b.download_button(tr("Télécharger", "Download"), latest.read_bytes(), file_name=latest.name,
                                  mime=KINDS[kind][1], icon=":material/download:", width="stretch", key=f"dl_latest_{kind}")
                st.caption(f"{latest.name} · {datetime.fromtimestamp(latest.stat().st_mtime):%Y-%m-%d %H:%M}")
            else:
                b.button(tr("Aucun fichier", "No file yet"), disabled=True, width="stretch", key=f"none_{kind}")

    every = sorted([*_latest("xlsx"), *_latest("pdf")], key=lambda p: p.stat().st_mtime, reverse=True)
    if len(every) > 1:
        with st.expander(tr(f"Rapports générés ({len(every)})", f"Generated reports ({len(every)})")):
            pick = st.selectbox(tr("Fichier", "File"), every[:30], format_func=lambda p: (
                f"{p.name} · {datetime.fromtimestamp(p.stat().st_mtime):%Y-%m-%d %H:%M} · {p.stat().st_size / 1024:.0f} Ko"
                if lang() == "fr" else
                f"{p.name} · {datetime.fromtimestamp(p.stat().st_mtime):%Y-%m-%d %H:%M} · {p.stat().st_size / 1024:.0f} KB"),
                key="rep_pick")
            st.download_button(tr("Télécharger ce rapport", "Download this report"), pick.read_bytes(), file_name=pick.name,
                               mime=XLSX if pick.suffix == ".xlsx" else "application/pdf", icon=":material/download:",
                               key="dl_pick")


def _datasets(df: pd.DataFrame, fc: pd.DataFrame, bt: list, ins: dict | None) -> None:
    st.html('<div class="cs-section" style="margin-top:8px">' + tr("Jeux de données", "Datasets") + "</div>")
    cols = st.columns(5)
    monthly = annual = None
    if ins:
        m = ins["monthly"].rename(columns={"s1": "scope1_t", "s2": "scope2_t", "other": "other_t", "total": "total_t"})
        m["month"] = m["month"].dt.strftime("%Y-%m")
        monthly = m.round(3).to_csv(index=False)
        annual = ins["annual"].rename(columns={"s1": "scope1_t", "s2": "scope2_t", "other": "other_t",
                                               "total": "total_t"}).round(3).to_csv(index=False)
    _download(cols[0], tr("Émissions journalières", "Daily emissions"), num(len(df), 0) + tr(" jours · CSV", " days · CSV"),
              df.to_csv(index=False), "carbonsync_emissions_daily.csv", "text/csv")
    _download(cols[1], tr("Synthèse mensuelle", "Monthly summary"), tr("scopes + activité · CSV", "scopes + activity · CSV"),
              monthly, "carbonsync_monthly.csv", "text/csv")
    _download(cols[2], tr("Synthèse annuelle", "Annual summary"), tr("scopes + intensité · CSV", "scopes + intensity · CSV"),
              annual, "carbonsync_annual.csv", "text/csv")
    _download(cols[3], tr("Prévision active", "Active forecast"), f"{len(fc)} " + tr("jours · CSV", "days · CSV"),
              fc.to_csv(index=False) if not fc.empty else None, "carbonsync_forecast.csv", "text/csv")
    _download(cols[4], tr("Backtest", "Backtest"), f"{len(bt)} " + tr("lignes · JSON", "rows · JSON"),
              json.dumps(bt, indent=2) if bt else None, "carbonsync_backtest.json", "application/json")


def _esrs() -> None:
    with ui.card(tr("Préparation ESRS E1-6", "ESRS E1-6 readiness")):
        ok, part = "✓ " + tr("Couvert", "Covered"), "◐ " + tr("Partiel", "Partial")
        checklist = [
            (tr("Scope 1 – émissions directes", "Scope 1 – direct emissions"), tr("Fioul et diesel, facteurs ADEME / GIEC", "Fuel oil and diesel, ADEME / IPCC factors"), ok),
            (tr("Scope 2 – méthode localisation", "Scope 2 – location-based"), tr("Réseau STEG 0,483 kgCO₂/kWh ; le solaire sur site réduit déjà les kWh achetés", "STEG grid 0.483 kgCO₂/kWh; on-site solar already lowers the kWh bought"), ok),
            (tr("Scope 2 – méthode marché", "Scope 2 – market-based"), tr("Instruments contractuels + mix résiduel (config.yaml → scope2) ; aucun contrat saisi, mix résiduel = moyenne réseau", "Contractual instruments + residual mix (config.yaml → scope2); no contract recorded, residual mix = grid average"), part),
            (tr("Scope 3 – catégories significatives", "Scope 3 – significant categories"), tr("Criblage : catégories 1, 3, 4, 5, 7, 9 estimées ; les autres exclues avec motif", "Screening: categories 1, 3, 4, 5, 7, 9 estimated; the others excluded with a reason"), part),
            (tr("Cible de réduction (E1-4)", "Reduction target (E1-4)"), tr("Année de référence, cible et trajectoire annuelle (Budget carbone)", "Base year, target and yearly path (Carbon budget)"), ok),
            (tr("Autres émissions", "Other emissions"), tr("Déchets et eau de procédé", "Waste and process water"), ok),
            (tr("Traçabilité des facteurs", "Factor traceability"), tr("Source et date de revue documentées", "Source and review date documented"), ok),
            (tr("Intensité carbone", "Carbon intensity"), tr("kgCO₂e par unité produite, par an et par mois", "kgCO₂e per unit produced, yearly and monthly"), ok),
        ]
        st.dataframe(
            pd.DataFrame(
                [{tr("Exigence", "Requirement"): r, tr("Détail", "Detail"): d, tr("Statut", "Status"): s}
                 for r, d, s in checklist]
            ),
            hide_index=True, width="stretch",
        )


def _download(col, title: str, sub: str, data: str | None, filename: str, mime: str) -> None:
    with col, ui.card(title, sub, stretch=True, align="distribute"):
        st.download_button(
            tr("Télécharger", "Download"), data or "", file_name=filename, mime=mime,
            icon=":material/download:", width="stretch", disabled=data is None, key=f"dl_{filename}",
        )
