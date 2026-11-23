"""Forecast – inspect, compare and (re)compute stored forecast runs."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from app.core.i18n import tr
from app.data.loader import (
    load_active_forecast,
    load_backtest_summary,
    load_config,
    load_emissions_daily,
    load_forecast_by_run,
    load_forecast_runs,
    load_latest_runs_by_model,
    set_active_run,
)
from app.ui import charts
from app.ui import components as ui
from app.ui.format import date_label, num


def _run_label(row: pd.Series) -> str:
    name = charts.MODEL_NAMES.get(row["model_name"], row["model_name"])
    created = pd.Timestamp(row["created_at"])
    return f"{name} · {row['history_days']} {tr('j', 'd')} · {date_label(created)} {created:%H:%M}"


def render() -> None:
    cfg = load_config()
    df = load_emissions_daily()
    runs = load_forecast_runs()
    if df.empty or runs.empty:
        ui.empty_state(tr("Aucune prévision enregistrée.", "No stored forecast yet."))
        _run_model_form()
        return

    fc, run = load_active_forecast()
    demo = cfg.get("app", {}).get("data_mode", "synthetic") == "synthetic"
    ui.page_header(
        tr("Pilotage", "Monitor"),
        tr("Prévisions", "Forecast"),
        tr("Horizon de 183 jours avec intervalle de confiance à 90 %.", "183-day horizon with a 90% confidence interval."),
        ui.data_chips(date_label(df["date"].max()), demo, charts.MODEL_NAMES.get(run["model_name"], run["model_name"])),
    )
    ui.forecast_freshness(run, key="fc")

    # ---- Controls --------------------------------------------------------------
    c1, c2, _ = st.columns([2.2, 1, 1.4], vertical_alignment="bottom")
    with c1:
        ids = list(runs["run_id"])
        labels = {r["run_id"]: _run_label(r) for _, r in runs.iterrows()}
        picked = st.selectbox(
            tr("Prévision active", "Active forecast"), ids, index=ids.index(run["run_id"]),
            format_func=labels.get, key="fc_run",
            help=tr("Utilisée sur toutes les pages.", "Used on every page."),
        )
        if picked != run["run_id"]:
            set_active_run(picked)
            st.rerun()
    with c2:
        weekly = st.segmented_control(
            tr("Pas de temps", "Step"), ["d", "w"], default="d",
            format_func=lambda v: tr("Jour", "Day") if v == "d" else tr("Semaine", "Week"), key="fc_step",
        ) == "w"

    # ---- KPIs --------------------------------------------------------------
    peak = fc.loc[fc["yhat"].idxmax()]
    ui.kpis(
        [
            ui.Kpi(tr("Total sur l'horizon", "Horizon total"), num(fc["yhat"].sum(), 0), "tCO₂e",
                   note=tr(f"{len(fc)} jours", f"{len(fc)} days")),
            ui.Kpi(tr("Moyenne journalière", "Daily average"), num(fc["yhat"].mean(), 2), tr("t/j", "t/d")),
            ui.Kpi(tr("Pic prévu", "Forecast peak"), num(peak["yhat"], 2), "t", note=date_label(peak["date"])),
            ui.Kpi(tr("Largeur moyenne 90 %", "Mean 90% width"), num((fc["hi90"] - fc["lo90"]).mean(), 2), "t"),
        ]
    )

    tab_traj, tab_cmp, tab_peaks = st.tabs(
        [tr("Trajectoire", "Trajectory"), tr("Comparer les modèles", "Compare models"), tr("Jours de pic", "Peak days")]
    )

    with tab_traj, ui.card(tr("Prévision et historique récent", "Forecast and recent history")):
        charts.show(charts.trajectory(df.tail(120), fc, weekly=weekly, height=420), key="fc_traj")

    with tab_cmp:
        _compare_tab(df)

    with tab_peaks, ui.card(tr("10 jours les plus émetteurs", "Top 10 highest-emission days")):
        top = fc.nlargest(10, "yhat")
        st.dataframe(
            pd.DataFrame(
                {
                    tr("Date", "Date"): top["date"].dt.strftime("%Y-%m-%d"),
                    tr("Prévision (t)", "Forecast (t)"): top["yhat"].round(2),
                    tr("Borne haute (t)", "Upper bound (t)"): top["hi90"].round(2),
                }
            ),
            hide_index=True, width="stretch",
        )


def _compare_tab(df: pd.DataFrame) -> None:
    latest = load_latest_runs_by_model()
    left, right = st.columns([2.4, 1])

    with left, ui.card(tr("Dernière exécution de chaque modèle", "Latest run of each model")):
        models = st.pills(
            "models", list(latest), default=list(latest), selection_mode="multi",
            format_func=lambda m: charts.MODEL_NAMES.get(m, m), label_visibility="collapsed", key="cmp_models",
        )
        bands = st.toggle(tr("Intervalles 90 %", "90% intervals"), value=False, key="cmp_bands")
        series = {m: load_forecast_by_run(str(latest[m]["run_id"])) for m in models or []}
        if series:
            charts.show(charts.model_overlay(series, df.tail(60), bands), key="cmp_chart")
        else:
            ui.empty_state(tr("Sélectionnez au moins un modèle.", "Select at least one model."))

    with right:
        with ui.card(tr("Précision en backtest", "Backtest accuracy"), tr("Moyenne des fenêtres", "Averaged over windows")):
            bt = pd.DataFrame(load_backtest_summary())
            if not bt.empty:
                agg = bt.groupby("model_name")[["mae", "mase", "coverage"]].mean().sort_values("mase")
                st.dataframe(
                    pd.DataFrame(
                        {
                            tr("Modèle", "Model"): [charts.MODEL_NAMES.get(m, m) for m in agg.index],
                            "MAE": agg["mae"].round(2).values,
                            "MASE": agg["mase"].round(2).values,
                            tr("Couv. 90 %", "90% cov."): (agg["coverage"] * 100).round(0).astype(int).astype(str) + "%",
                        }
                    ),
                    hide_index=True, width="stretch",
                )
                st.caption(tr("MASE < 1 = meilleur que la référence naïve.", "MASE < 1 beats the naive baseline."))
        _run_model_form()


def _run_model_form() -> None:
    with st.popover(tr("Calculer une nouvelle prévision", "Run a new forecast"), icon=":material/play_arrow:", width="stretch"):
        model = st.selectbox(
            tr("Modèle", "Model"), ["auto", "ens_pc", "chronos", "prophet"],
            format_func=lambda m: tr("Auto (routeur)", "Auto (router)") if m == "auto" else charts.MODEL_NAMES[m],
            key="run_model",
        )
        days = st.select_slider(
            tr("Historique d'entraînement", "Training history"), [180, 365, 730, 1095], value=730,
            format_func=lambda d: tr(f"{d} jours", f"{d} days"), key="run_days",
        )
        st.caption(tr("Chronos-2 peut prendre 1 à 2 min sur CPU.", "Chronos-2 can take 1–2 min on CPU."))
        from app.core import auth

        allowed = auth.can("forecast_run")
        if st.button(tr("Lancer", "Run"), type="primary", width="stretch", key="run_go", disabled=not allowed,
                     help=None if allowed else auth.need("forecast_run")):
            with st.spinner(tr("Calcul en cours…", "Running…")):
                try:
                    from carbonsync.models.ensemble import run_and_save_forecast

                    res = run_and_save_forecast(history_days=days, model_name=model, horizon_days=183)
                except Exception as exc:  # noqa: BLE001 - shown to the user, not swallowed
                    st.error(f"{type(exc).__name__}: {exc}")
                    return
            st.cache_data.clear()
            set_active_run(res["run_id"])
            st.toast(tr("Prévision enregistrée.", "Forecast saved."), icon=":material/check:")
            st.rerun()
