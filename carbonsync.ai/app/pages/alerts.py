"""Alerts – what needs attention: budget, year-end, unusual days, late data, quarantine.

The pipeline checks them after each upload; editors can also check now. Saved in ``data/alerts.json``.
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from app.core import auth, nav
from app.core.i18n import lang, tr
from app.data.loader import (
    clear_caches,
    load_alerts_state,
    load_config,
    load_emissions_daily,
)
from app.ui import charts, inv_charts
from app.ui import components as ui
from app.ui.format import date_label, num

SEV = {"critical": ":material/error:", "warning": ":material/warning:", "info": ":material/info:"}
ORDER = {"critical": 0, "warning": 1, "info": 2}
# Critical cards on dark red (white text 9.9:1), warnings tinted amber, info neutral: what to act on stands out
_CSS = """<style>
[class*="st-key-cs_al_"] { border-radius: 14px; padding: 14px 16px 12px 18px; gap: .35rem;
  border: 1px solid var(--cs-line); border-left-width: 6px; background: var(--cs-surface, var(--cs-fill)); }
[class*="st-key-cs_al_critical_"] { background: #7f1d1d !important; border-color: #991b1b; border-left-color: #ef4444;
  box-shadow: 0 10px 28px rgba(127,29,29,.35); }
[class*="st-key-cs_al_critical_"] :is(p, span, div, a, small, strong, [data-testid="stCaptionContainer"]) { color: #fff !important; }
[class*="st-key-cs_al_critical_"] [data-testid="stCaptionContainer"] { opacity: .85; }
[class*="st-key-cs_al_critical_"] [data-testid="stPageLink"] a { border: 1px solid rgba(255,255,255,.55); border-radius: 10px;
  background: rgba(255,255,255,.10); }
[class*="st-key-cs_al_warning_"] { background: rgba(250,178,25,.13); border-left-color: #f59e0b; }
[class*="st-key-cs_al_info_"] { border-left-color: var(--cs-line); }
</style>"""
PAGE_LABELS = {"budget": ("Budget carbone", "Carbon budget"), "intake": ("Dépôt de données", "Data intake"),
               "data": ("Qualité des données", "Data quality"), "methodology": ("Méthodologie", "Methodology"),
               "alerts": ("Alertes", "Alerts")}


@st.cache_data(show_spinner=False, ttl=300)
def _band(stamp: str) -> pd.DataFrame:   # stamp: alerts check time, part of the cache key
    from carbonsync import alerts as core

    return core.band()


def render() -> None:
    from carbonsync import alerts as core

    cfg = load_config()
    state = load_alerts_state()
    df = load_emissions_daily()
    L = lang()
    checked = state.get("checked_at")
    ui.page_header(
        tr("Alertes", "Alerts"), tr("Alertes", "Alerts"),
        tr("Vérifiées après chaque exécution du pipeline, ou à la demande.",
           "Checked after each pipeline run, or on demand."),
        [(tr(f"Vérifiées le {checked.replace('T', ' ')}", f"Checked {checked.replace('T', ' ')}"), "")] if checked
        else [(tr("Jamais vérifiées", "Never checked"), "warn")],
    )
    counts = core.counts(state)
    st.html(_CSS)
    c1, c2 = st.columns([4, 1], vertical_alignment="center")
    with c1:
        ui.kpis([
            ui.Kpi(tr("Critiques", "Critical"), str(counts["critical"]), tone="bad" if counts["critical"] else "good",
                   delta=tr("à traiter", "to act on") if counts["critical"] else tr("aucune", "none"),
                   kind="crit" if counts["critical"] else ""),
            ui.Kpi(tr("À surveiller", "Warnings"), str(counts["warning"])),
            ui.Kpi(tr("Informations", "Info"), str(counts["info"])),
        ])
    with c2:
        if st.button(tr("Vérifier maintenant", "Check now"), icon=":material/refresh:", type="primary",
                     disabled=not auth.can("alerts_check"), help=None if auth.can("alerts_check") else auth.need("alerts_check"),
                     key="al_check", width="stretch"):
            with st.spinner(tr("Vérification…", "Checking…")):
                core.check(trigger="manual")
            clear_caches()
            st.rerun()

    alerts = sorted(state.get("alerts", []), key=lambda a: ORDER.get(a.get("severity"), 3))
    if not alerts:
        ui.status_banner("green", tr("Aucune alerte ouverte.", "No open alert."), tr("Tout va bien", "All clear"))
    else:
        names = {"all": tr(f"Toutes ({len(alerts)})", f"All ({len(alerts)})"),
                 "critical": tr(f"Critiques ({counts['critical']})", f"Critical ({counts['critical']})"),
                 "warning": tr(f"À surveiller ({counts['warning']})", f"Warnings ({counts['warning']})"),
                 "info": tr(f"Informations ({counts['info']})", f"Info ({counts['info']})")}
        if st.session_state.get("al_filter") not in names:
            st.session_state["al_filter"] = "all"       # the floating alert button may set "critical"
        shown = st.segmented_control(tr("Afficher", "Show"), list(names), format_func=names.get, key="al_filter",
                                     label_visibility="collapsed") or "all"
        alerts = [a for a in alerts if shown == "all" or a.get("severity") == shown]
        if not alerts:
            st.caption(tr("Aucune alerte de ce niveau.", "No alert at this level."))
    for i, a in enumerate(alerts):
        icon = SEV.get(a["severity"], SEV["info"])
        with st.container(key=f"cs_al_{a.get('severity', 'info')}_{i}"):
            left, right = st.columns([5, 1.4], vertical_alignment="center")
            with left:
                sev = tr(*core.SEVERITY_LABELS[a["severity"]])
                st.markdown(f"{icon} **{a['title_' + L]}**  \n{a['detail_' + L]}")
                since = str(a.get("first_seen", ""))[:16].replace("T", " ")
                st.caption(f"{sev} · " + tr(f"depuis le {since}", f"since {since}"))
            with right:
                page = a.get("page")
                if page and page != "alerts" and page in PAGE_LABELS:
                    nav.link(page, tr(*PAGE_LABELS[page]), icon=":material/arrow_forward:")

    with ui.card(tr("Jours hors de la bande de prévision", "Days outside the forecast band"),
                 tr("Chaque jour réel comparé à la prévision faite avant lui (bande 90 %). Un point rouge = un jour "
                    "inhabituel à expliquer (arrêt, panne, erreur de saisie…).",
                    "Each actual day against the forecast made before it (90% band). A red dot = an unusual day "
                    "to explain (shutdown, breakdown, data entry error…).")):
        band = _band(checked or "")
        if band.empty:
            st.caption(tr("Pas encore de prévision antérieure aux derniers jours : relancer le pipeline après le "
                          "prochain dépôt.", "No forecast made before the latest days yet: rerun the pipeline after "
                          "the next upload."))
        else:
            recent = df[df["date"] >= band["date"].min()]
            out = band[(band["actual"] > band["hi90"]) | (band["actual"] < band["lo90"])]
            charts.show(inv_charts.anomaly_chart(recent, band, out), key="al_band")
            inside = 100 - len(out) / len(band) * 100
            st.caption(tr(f"{len(band)} jours comparés · {num(inside, 0)} % dans la bande (attendu ≈ 90 %).",
                          f"{len(band)} days compared · {num(inside, 0)}% inside the band (expected ≈ 90%)."))

    hist = state.get("history", [])
    if hist:
        with st.expander(tr(f"Alertes résolues ({len(hist)})", f"Resolved alerts ({len(hist)})")):
            st.dataframe(pd.DataFrame([{
                tr("Alerte", "Alert"): h.get("title_" + L, ""),
                tr("Gravité", "Severity"): tr(*core.SEVERITY_LABELS.get(h.get("severity"), ("—", "—"))),
                tr("Apparue", "Raised"): str(h.get("first_seen", ""))[:16].replace("T", " "),
                tr("Résolue", "Resolved"): str(h.get("resolved_at", ""))[:16].replace("T", " "),
            } for h in hist[:50]]), hide_index=True, width="stretch")

    s = core.settings(cfg)
    st.caption(tr(f"Seuils (config.yaml, alerts) : aucune donnée depuis {s['stale_after_days']} jours, fenêtre "
                  f"d'anomalies {s['anomaly_window_days']} jours. Envoi par e-mail non configuré (optionnel). "
                  f"Données au {date_label(df['date'].max())}." if not df.empty else "",
                  f"Thresholds (config.yaml, alerts): no data for {s['stale_after_days']} days, anomaly window "
                  f"{s['anomaly_window_days']} days. Email sending not configured (optional). "
                  f"Data through {date_label(df['date'].max())}." if not df.empty else ""))
