"""Data intake – drop department CSVs (per department, many at once, a folder or a .zip) and rerun the whole loop.

Loop: save to inbox → validate & repair → DuckDB → daily emissions → router forecast
(Chronos-2 from 60 days of history, otherwise Prophet) → backtest refresh → alerts → reports →
every page refreshes. Editors can upload; going back to the original data needs an administrator.
"""
from __future__ import annotations

import re

import pandas as pd
import streamlit as st

from app.core import auth
from app.core.i18n import tr
from app.data.loader import (
    clear_caches,
    load_config,
    load_department_last_dates,
    load_department_status,
    load_emissions_daily,
    load_quarantine_details,
)
from app.ui import charts, lab_charts
from app.ui import components as ui
from app.ui.format import date_label, num

DEPT_ICONS = {"energie": "bolt", "production": "precision_manufacturing", "logistique": "local_shipping",
              "dechets_eau": "water_drop"}


def _dept(d: str) -> str:
    return tr(*lab_charts.DEPT_LABELS[d])


def render() -> None:
    from carbonsync.ingest import intake
    from carbonsync.ingest.schemas import EXPECTED_COLUMNS

    cfg = load_config()
    df = load_emissions_daily()
    last_dates = load_department_last_dates()
    status = load_department_status()
    through = df["date"].max() if not df.empty else None
    demo = cfg.get("app", {}).get("data_mode", "synthetic") == "synthetic"
    nonce = st.session_state.setdefault("intake_nonce", 0)

    ui.page_header(
        tr("Données", "Data"),
        tr("Dépôt de données", "Data intake"),
        tr("Déposez les CSV des services : tout le pipeline se relance et chaque page se met à jour.",
           "Drop department CSVs: the whole pipeline reruns and every page updates."),
        ui.data_chips(date_label(through) if through is not None else "—", demo),
    )
    _last_result()

    # ---- Per-department intake ------------------------------------------------
    staged: list = []
    cols = st.columns(4)
    for col, d in zip(cols, intake.DEPARTMENTS):
        with col, st.container(border=True):
            last = last_dates.get(d)
            if last is None:
                state, note = "none", tr("Aucune donnée", "No data")
            elif through is not None and last > through:
                state, note = "ahead", tr("En attente des autres services", "Waiting for other departments")
            elif through is not None and last < through:
                state, note = "behind", tr(f"En retard de {(through - last).days} j", f"{(through - last).days} d behind")
            else:
                state, note = "ok", tr("À jour", "Up to date")
            ui.dept_tile(_dept(d), date_label(last) if last is not None else "—", state, note)
            files = st.file_uploader(
                _dept(d), type=["csv"], accept_multiple_files=True, key=f"up_{d}_{nonce}",
                label_visibility="collapsed",
            )
            for f in files or []:
                staged.append(intake.inspect_upload(f.name, f.getvalue(), department=d))
            template = ",".join(EXPECTED_COLUMNS[d]) + "\n"
            st.download_button(tr("Modèle CSV", "CSV template"), template, file_name=f"{d}_{tr('AAAA-MM', 'YYYY-MM')}.csv",
                               mime="text/csv", icon=":material/download:", width="stretch", key=f"tpl_{d}",
                               type="tertiary")

    # ---- Bulk intake -------------------------------------------------------------
    with ui.card(tr("Tout déposer en une fois", "Drop everything at once"),
                 tr("Plusieurs CSV, un .zip ou un dossier : le service est reconnu par le nom du fichier "
                    "(energie_, production_, logistique_, dechets_eau_) ou par ses colonnes.",
                    "Several CSVs, a .zip or a folder: the department is recognised from the file name "
                    "(energie_, production_, logistique_, dechets_eau_) or from its columns.")):
        kind = st.segmented_control(
            "bulk_kind", ["files", "folder"], default="files", label_visibility="collapsed",
            format_func=lambda v: tr("Fichiers ou .zip", "Files or .zip") if v == "files" else tr("Dossier", "Folder"),
            key="bulk_kind",
        ) or "files"
        bulk = st.file_uploader(
            tr("Fichiers", "Files"), type=["csv", "zip"] if kind == "files" else ["csv"],
            accept_multiple_files=True if kind == "files" else "directory",
            key=f"up_bulk_{kind}_{nonce}", label_visibility="collapsed",
        )
        for f in bulk or []:
            try:
                for name, content in intake.expand_archive(f.name, f.getvalue()):
                    staged.append(intake.inspect_upload(name, content))
            except Exception as exc:  # noqa: BLE001 - a corrupt archive must not break the page
                st.error(f"{f.name}: {exc}")

    # ---- Staging + run -------------------------------------------------------------
    unique = {it.target_name: it for it in staged}  # the same file dropped twice: keep the last one
    items = list(unique.values())
    _staging(items)
    _actions(items, intake)

    # ---- Coverage ----------------------------------------------------------------
    _coverage(status, last_dates)

    # ---- Uploaded files & history --------------------------------------------------
    left, right = st.columns([1.1, 1])
    with left, ui.card(tr("Fichiers déposés manuellement", "Manually uploaded files")):
        uploads = intake.list_uploads()
        if uploads:
            st.dataframe(pd.DataFrame([{
                tr("Fichier", "File"): u["file"], tr("Service", "Department"): _dept(u["department"]),
                tr("Lignes", "Rows"): u["rows"], tr("Période", "Period"): f"{u['first_date']} → {u['last_date']}",
                tr("Déposé", "Uploaded"): u["uploaded_at"].replace("T", " "),
            } for u in reversed(uploads)]), hide_index=True, width="stretch", height=min(38 + 35 * len(uploads), 320))
            _reset(intake, len(uploads))
        else:
            st.caption(tr("Aucun pour l'instant. Exemples prêts à déposer : dossier samples/ du projet.",
                          "None yet. Ready-to-upload examples: the project's samples/ folder."))
    with right, ui.card(tr("Historique du pipeline", "Pipeline history")):
        from carbonsync.pipeline import read_history

        hist = read_history(15)
        if hist:
            st.dataframe(pd.DataFrame([{
                tr("Début", "Started"): h["started_at"].replace("T", " ")[:16],
                tr("Origine", "Trigger"): h["trigger"],
                tr("Jours", "Days"): f"{h['before']['days']} → {h['after']['days']}",
                tr("Jusqu'au", "Through"): h["after"]["last"],
                tr("Modèle", "Model"): charts.MODEL_NAMES.get(h["steps"].get("forecast", {}).get("model"), "—"),
                tr("Durée (s)", "Time (s)"): h["seconds"],
            } for h in hist]), hide_index=True, width="stretch")
        else:
            st.caption(tr("Le pipeline n'a pas encore été relancé depuis le tableau de bord.",
                          "The pipeline has not been rerun from the dashboard yet."))


# ---------------------------------------------------------------------------
def _staging(items: list) -> None:
    if not items:
        return
    with ui.card(tr(f"Prêt à intégrer · {len(items)} fichier(s)", f"Ready to ingest · {len(items)} file(s)"),
                 tr("Vérification rapide avant validation complète.", "Quick check before full validation.")):
        st.dataframe(pd.DataFrame([{
            "": "✓" if it.ok else "✗",
            tr("Fichier", "File"): it.original_name,
            tr("Enregistré sous", "Saved as"): it.target_name,
            tr("Service", "Department"): _dept(it.department) if it.department else "—",
            tr("Détecté via", "Detected by"): {"name": tr("nom", "name"), "columns": tr("colonnes", "columns"),
                                               "chosen": tr("zone de dépôt", "drop zone")}.get(it.detected_from, "—"),
            tr("Lignes", "Rows"): it.rows,
            tr("Période", "Period"): f"{it.first_date} → {it.last_date}" if it.first_date else "—",
            tr("Remarques", "Notes"): " · ".join(it.notes),
        } for it in items]), hide_index=True, width="stretch", height=min(38 + 35 * len(items), 360))


def _actions(items: list, intake) -> None:
    ok = [it for it in items if it.ok]
    c1, c2, c3 = st.columns([1.4, 1, 1], vertical_alignment="center")
    with c1:
        go = st.button(
            tr(f"Intégrer {len(ok)} fichier(s) et relancer le pipeline", f"Ingest {len(ok)} file(s) and rerun the pipeline")
            if ok else tr("Déposez des fichiers pour commencer", "Drop files to start"),
            type="primary", icon=":material/play_arrow:", width="stretch", disabled=not ok, key="intake_go",
        )
    with c2:
        rerun = st.button(tr("Relancer sans nouveau fichier", "Rerun without new files"), icon=":material/refresh:",
                          width="stretch", key="intake_rerun")
    with c3:
        report = st.toggle(tr("Générer le rapport Excel", "Generate the Excel report"), value=True, key="intake_report")
    if go or rerun:
        _run_pipeline(intake, ok if go else [], report, "upload" if go else "manual")


def _run_pipeline(intake, items: list, report: bool, trigger: str) -> None:
    from carbonsync.pipeline import run_pipeline

    try:
        with ui.live_log(tr("Pipeline en cours…", "Pipeline running…"), tr("Pipeline terminé", "Pipeline complete")):
            who = auth.current()
            saved = intake.save_uploads(items, uploaded_by=None if who.get("local") else who["username"]) if items else []
            summary = run_pipeline(forecast=True, report=report, trigger=trigger)
    except Exception as exc:  # noqa: BLE001 - shown to the user
        st.error(f"{type(exc).__name__}: {exc}")
        return
    clear_caches()
    fc = summary["steps"].get("forecast")
    if fc:
        st.session_state["active_run_id"] = fc["run_id"]
    summary["saved"] = saved
    st.session_state["intake_result"] = summary
    st.session_state["intake_nonce"] = st.session_state.get("intake_nonce", 0) + 1  # empties the drop zones
    st.rerun()


def _last_result() -> None:
    res = st.session_state.pop("intake_result", None)
    if not res:
        return
    before, after = res["before"], res["after"]
    fc = res["steps"].get("forecast", {})
    v = res["steps"]["validate"]
    if res.get("trigger") == "reset":
        what = tr("Données d'origine restaurées", "Original data restored")
    else:
        what = tr(f"{len(res.get('saved', []))} fichier(s) intégré(s)", f"{len(res.get('saved', []))} file(s) ingested")
    msg = tr(
        f"{what} · {num(before['days'], 0)} → {num(after['days'], 0)} jours "
        f"({res['new_days']:+d}) · données jusqu'au {after['last']} · prévision {charts.MODEL_NAMES.get(fc.get('model'), '—')} "
        f"· {v['rejected_count']} fichier(s) en quarantaine · {res['seconds']} s",
        f"{what} · {num(before['days'], 0)} → {num(after['days'], 0)} days "
        f"({res['new_days']:+d}) · data through {after['last']} · forecast {charts.MODEL_NAMES.get(fc.get('model'), '—')} "
        f"· {v['rejected_count']} file(s) quarantined · {res['seconds']} s",
    )
    ui.status_banner("green", msg, tr("Pipeline terminé", "Pipeline complete"))
    pending = res["steps"]["features"].get("pending_departments") or {}
    if pending:
        ui.status_banner("amber", label=tr("En attente", "Waiting"), message=tr(
            "Mois incomplet, en attente de : " + ", ".join(_dept(d) for d in pending)
            + ". Les jours déjà reçus seront utilisés dès que tous les services auront transmis.",
            "Incomplete month, waiting for: " + ", ".join(_dept(d) for d in pending)
            + ". Days already received are used as soon as every department has reported.",
        ))
    if "report" in res["steps"]:
        rep = res["steps"]["report"]
        st.caption(tr("Nouveaux rapports : ", "New reports: ") + rep["file"] + (f" · {rep['brief']}" if rep.get("brief") else ""))


def _coverage(status: pd.DataFrame, last_dates: dict) -> None:
    known = [d for d in last_dates.values() if d is not None]
    if not known:
        return
    end = max(known).to_period("M").to_timestamp()
    months = pd.date_range(end - pd.DateOffset(months=17), end, freq="MS")
    rejected = set()
    for q in load_quarantine_details():
        m = re.search(r"(\d{4}-\d{2})", q.get("file_name", ""))
        if m and q.get("department"):
            rejected.add((q["department"], pd.Timestamp(m.group(1) + "-01")))
    with ui.card(tr("Couverture par service · 18 derniers mois", "Coverage by department · last 18 months"),
                 tr("Vert : complet · Orange : partiel · Rouge : rejeté (voir Qualité des données) · Gris : absent",
                    "Green: complete · Orange: partial · Red: rejected (see Data quality) · Gray: missing")):
        charts.show(lab_charts.dept_coverage(status, rejected, months), key="intake_cov")


def _reset(intake, n: int) -> None:
    with st.popover(tr("Revenir aux données d'origine", "Back to the original data"), icon=":material/restore:"):
        st.caption(tr(f"Supprime les {n} fichier(s) déposé(s), restaure les originaux remplacés et relance le pipeline.",
                      f"Removes the {n} uploaded file(s), restores replaced originals and reruns the pipeline."))
        if st.button(tr("Confirmer", "Confirm"), type="primary", key="intake_reset", width="stretch",
                     disabled=not auth.can("reset_data"),
                     help=None if auth.can("reset_data") else auth.need("reset_data")):
            from carbonsync.pipeline import run_pipeline

            try:
                with ui.live_log(tr("Réinitialisation…", "Resetting…"), tr("Données d'origine restaurées", "Original data restored")):
                    intake.remove_uploads()
                    summary = run_pipeline(forecast=True, report=False, trigger="reset")
            except Exception as exc:  # noqa: BLE001
                st.error(f"{type(exc).__name__}: {exc}")
                return
            clear_caches()
            st.session_state.pop("active_run_id", None)
            summary["saved"] = []
            st.session_state["intake_result"] = summary
            st.session_state["intake_nonce"] = st.session_state.get("intake_nonce", 0) + 1
            st.rerun()
